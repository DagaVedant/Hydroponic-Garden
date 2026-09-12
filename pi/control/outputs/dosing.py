"""Nutrient and pH dosing.

This is the only code in the project that can destroy a tank. A stuck pump empties
a bottle of pH Down into 16 L of solution and kills sixteen plants in an afternoon.
Everything below is arranged around that.

**It will not dose real chemicals using guessed numbers.** DOSING_CALIBRATED is
False until three constants have been measured on the bench, and while it is False
the controller runs happily in simulation and refuses to touch a pump on hardware.
The constants cannot be derived, only measured:

    DOSE_FLOW_ML_PER_S    run each pump 60s into a measuring cylinder
    EC_PER_ML_*           dose 5 mL, wait for the tank to mix, read the delta
    PH_PER_ML_DOWN        same, and it is non-linear because the solution buffers

The state machine:

    IDLE ---(something is out of band)---> DOSING
    DOSING ---(pump ran)---> MIXING
    MIXING ---(tank has had time to mix)---> VERIFYING
    VERIFYING ---(the reading moved)---> IDLE
    VERIFYING ---(it did not)---> FAULT

FAULT is sticky and needs a human. A dose that does not show up in the sensor means
an empty bottle, a slipped tube or a dead pump, and the correct response to any of
those is to stop, not to dose harder. This is what replaced the float switches: a
float switch only catches the empty bottle.
"""

from __future__ import annotations

import collections
import time
from typing import Callable, Deque, Dict, Optional, Tuple

from ..config import (DOSE_EC_TARGET, DOSE_EC_DEADBAND, DOSE_FLOW_ML_PER_S,
                      DOSE_MAX_ML_PER_EVENT, DOSE_MAX_ML_PH_EVENT,
                      DOSE_MAX_SECONDS_PER_HOUR,
                      DOSE_MIN_INTERVAL_S, DOSE_MIN_LEVEL_L, DOSE_MIX_WAIT_S,
                      DOSE_PH_TARGET, DOSE_PH_DEADBAND, DOSE_VERIFY_FRACTION,
                      DOSE_VERIFY_MAX_FRACTION,
                      DOSING_CALIBRATED, DOSING_ENABLED, EC_PER_ML, MICRO_GRO_RATIO,
                      PH_PER_ML_DOWN)
from .pwm import Channel

IDLE, DOSING, MIXING, VERIFYING, FAULT = "idle", "dosing", "mixing", "verifying", "fault"

# Channel names. "micro" is FloraMicro, "gro" is FloraGro, "ph_down" is pH Down.
MICRO, GRO, PH_DOWN = "micro", "gro", "ph_down"


class Doser:
    def __init__(self, channels: Dict[str, Channel], simulate: bool = False,
                 on_dose: Optional[Callable[[str, float], None]] = None) -> None:
        self.ch = channels
        self.simulate = simulate
        self.on_dose = on_dose                 # lets the simulated tank respond

        self.state = IDLE
        self.fault_reason = ""
        self.enabled = DOSING_ENABLED

        self._runtime: Deque[Tuple[float, float]] = collections.deque()   # (ts, secs)
        self._last_dose_ts = 0.0
        self._pending: Optional[dict] = None   # what we are waiting to verify
        self._mix_until = 0.0
        self._totals: Dict[str, float] = {MICRO: 0.0, GRO: 0.0, PH_DOWN: 0.0}

    # ------------------------------------------------------------------ safety

    def runtime_last_hour(self) -> float:
        cutoff = time.time() - 3600
        while self._runtime and self._runtime[0][0] < cutoff:
            self._runtime.popleft()
        return sum(s for _, s in self._runtime)

    def interlocks(self, ec, ph, level, water_temp) -> Optional[str]:
        """Every reason not to dose. Returns the first one, or None to proceed.

        Order matters only for the message. Any one of these blocking is enough.
        """
        if not self.enabled:
            return "dosing disabled"
        if self.state == FAULT:
            return f"in fault: {self.fault_reason}"
        if not self.simulate and not DOSING_CALIBRATED:
            return "pumps not calibrated, refusing to dose on hardware"
        if ec is None:
            return "no valid ec reading"
        if ph is None:
            return "no valid ph reading"
        if water_temp is None:
            return "no water temp, ec is uncompensated"
        if level is None:
            return "no valid level reading"
        if level < DOSE_MIN_LEVEL_L:
            return f"level {level:.1f} L below the {DOSE_MIN_LEVEL_L} L floor"
        if time.time() - self._last_dose_ts < DOSE_MIN_INTERVAL_S:
            return "too soon since the last dose"
        if self.runtime_last_hour() >= DOSE_MAX_SECONDS_PER_HOUR:
            return (f"hourly cap reached, {self.runtime_last_hour():.0f}s of "
                    f"{DOSE_MAX_SECONDS_PER_HOUR}s")
        return None

    # ------------------------------------------------------------------ decide

    def _next_nutrient(self) -> str:
        """Which of the two nutrient parts is furthest behind its ratio.

        FloraMicro goes in first and alone. Micro and Gro added together without
        mixing between them precipitates calcium phosphate and locks the nutrients
        out, so only ever one channel per dose event.
        """
        micro, gro = self._totals[MICRO], self._totals[GRO]
        if micro <= 0:
            return MICRO
        return GRO if (gro / micro) < MICRO_GRO_RATIO else MICRO

    def decide(self, ec: float, ph: float) -> Optional[Tuple[str, float, str, float]]:
        """What to dose, how much, which sensor should move and by how much.

        EC is corrected before pH, because adding nutrients moves pH and fixing pH
        first just means doing it twice.
        """
        if ec < DOSE_EC_TARGET - DOSE_EC_DEADBAND:
            channel = self._next_nutrient()
            shortfall = DOSE_EC_TARGET - ec
            ml = min(shortfall / EC_PER_ML[channel], DOSE_MAX_ML_PER_EVENT)
            return channel, ml, "water/ec", ml * EC_PER_ML[channel]

        if ph > DOSE_PH_TARGET + DOSE_PH_DEADBAND:
            excess = ph - DOSE_PH_TARGET
            # its own cap. overshooting ph is much easier to do than overshooting
            # ec, and much harder to walk back once the solution is acidified
            ml = min(excess / PH_PER_ML_DOWN, DOSE_MAX_ML_PH_EVENT)
            return PH_DOWN, ml, "water/ph", -(ml * PH_PER_ML_DOWN)

        return None

    # ------------------------------------------------------------------ act

    def _run_pump(self, channel: str, ml: float) -> float:
        seconds = ml / DOSE_FLOW_ML_PER_S[channel]
        if self.simulate:
            time.sleep(0.01)                       # do not really wait in a test
            actual = seconds
        else:
            actual = self.ch[channel].pulse(seconds)
        self._runtime.append((time.time(), actual))
        self._last_dose_ts = time.time()
        self._totals[channel] += ml
        if self.on_dose:
            self.on_dose(channel, ml)
        return actual

    def update(self, ec, ph, level, water_temp) -> dict:
        """Call once per sweep. Returns what happened, for logging and mqtt."""
        now = time.time()

        # ---- waiting for the tank to mix
        if self.state == MIXING:
            if now < self._mix_until:
                return {"state": MIXING, "note": f"{self._mix_until - now:.0f}s left"}
            self.state = VERIFYING
            return {"state": VERIFYING, "note": "reading back"}

        # ---- did the dose actually show up
        if self.state == VERIFYING:
            p = self._pending or {}
            actual_now = ec if p.get("sensor") == "water/ec" else ph
            if actual_now is None:
                self.state = FAULT
                self.fault_reason = "sensor invalid during verification"
                return {"state": FAULT, "note": self.fault_reason}

            moved = actual_now - p.get("before", actual_now)
            expected = p.get("expected", 0.0)
            # A ratio rather than a difference, so one test covers both directions:
            # ph down moves the reading down and nutrients move it up, and dividing
            # two same-sign numbers gives a positive fraction either way.
            ratio = (moved / expected) if expected else 1.0
            self._pending = None

            if ratio < DOSE_VERIFY_FRACTION:
                self.state = FAULT
                self.fault_reason = (f"{p.get('channel')} dose did not land: expected "
                                     f"{expected:+.3g}, saw {moved:+.3g}. empty bottle, "
                                     f"slipped tube or dead pump")
                return {"state": FAULT, "note": self.fault_reason}

            # Too much movement is a failed dose as well. Checking only the lower
            # bound calls a pH crash from 6.9 to 2.0 a success, because it certainly
            # moved far enough. A pump that did not stop, or a flow rate measured
            # wrong, both land here, and both get worse if the answer is to dose on.
            if ratio > DOSE_VERIFY_MAX_FRACTION:
                self.state = FAULT
                self.fault_reason = (f"{p.get('channel')} dose overshot: expected "
                                     f"{expected:+.3g}, saw {moved:+.3g}. the pump did "
                                     f"not stop, or the flow rate is miscalibrated")
                return {"state": FAULT, "note": self.fault_reason}

            self.state = IDLE
            return {"state": IDLE, "note": f"verified, moved {moved:+.3g}"}

        # ---- idle: should we dose
        blocked = self.interlocks(ec, ph, level, water_temp)
        if blocked:
            return {"state": self.state, "note": blocked}

        plan = self.decide(ec, ph)
        if plan is None:
            return {"state": IDLE, "note": "in band"}

        channel, ml, sensor, expected = plan
        before = ec if sensor == "water/ec" else ph

        self.state = DOSING
        seconds = self._run_pump(channel, ml)

        self._pending = {"channel": channel, "sensor": sensor,
                         "before": before, "expected": expected, "ml": ml}
        self._mix_until = time.time() + DOSE_MIX_WAIT_S
        self.state = MIXING

        return {"state": MIXING, "channel": channel, "ml": round(ml, 2),
                "seconds": round(seconds, 2),
                "note": f"dosed {ml:.1f}mL of {channel}, mixing"}

    # ------------------------------------------------------------------ misc

    def clear_fault(self) -> None:
        self.state = IDLE
        self.fault_reason = ""
        self._pending = None

    def state_dict(self) -> dict:
        return {
            "state": self.state,
            "enabled": self.enabled,
            "calibrated": DOSING_CALIBRATED,
            "fault": self.fault_reason,
            "runtime_last_hour_s": round(self.runtime_last_hour(), 1),
            "cap_s": DOSE_MAX_SECONDS_PER_HOUR,
            "totals_ml": {k: round(v, 2) for k, v in self._totals.items()},
        }

    def close(self) -> None:
        for ch in self.ch.values():
            ch.close()
