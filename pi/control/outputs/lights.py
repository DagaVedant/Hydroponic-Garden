"""Photoperiod and dimming for the led strip.

Nothing here needs a measured constant. The strip is 17 W/m over 3.2m, about 54 W,
switched by the led mosfet on the hat with pwm.

Three behaviours:

- **photoperiod** -- on at LIGHT_ON_HOUR, off at LIGHT_OFF_HOUR, handling the case
  where the window crosses midnight
- **ramp** -- fade in and out over a few minutes rather than snapping. plants do not
  care much, but the inrush on 54 W of led does, and a room that does not flash on
  at 6am is nicer to live with
- **manual override** -- `hydro/cmd/lights` can force a duty or hand control back

The pump is *not* tied to this. It runs continuously off its own gfci outlet and
nothing in software can stop it.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from ..config import (LIGHT_BRIGHTNESS, LIGHT_OFF_HOUR, LIGHT_ON_HOUR,
                      LIGHT_RAMP_MINUTES)
from .pwm import Channel


def _minutes(t: dt.datetime) -> float:
    return t.hour * 60 + t.minute + t.second / 60.0


class Lights:
    def __init__(self, channel: Channel) -> None:
        self.ch = channel
        self.override: Optional[float] = None      # None means follow the schedule

    # ------------------------------------------------------------------ schedule

    @staticmethod
    def scheduled_duty(now: dt.datetime,
                       on_hour: float = LIGHT_ON_HOUR,
                       off_hour: float = LIGHT_OFF_HOUR,
                       brightness: float = LIGHT_BRIGHTNESS,
                       ramp_minutes: float = LIGHT_RAMP_MINUTES) -> float:
        """What the duty should be right now, 0.0 to 1.0.

        Pure function of the clock so it can be tested at any hour without waiting
        for that hour to come around.
        """
        on_m, off_m = on_hour * 60.0, off_hour * 60.0
        now_m = _minutes(now)
        day = 24 * 60.0

        # how far into the lit window are we, in minutes, or None if outside it
        if on_m <= off_m:
            lit = on_m <= now_m < off_m
            since_on = now_m - on_m
            until_off = off_m - now_m
        else:
            # window wraps midnight, e.g. on 20:00 off 12:00
            lit = now_m >= on_m or now_m < off_m
            since_on = now_m - on_m if now_m >= on_m else now_m + (day - on_m)
            until_off = off_m - now_m if now_m < off_m else off_m + (day - now_m)

        if not lit:
            return 0.0
        if ramp_minutes <= 0:
            return brightness

        # linear fade at each end, clamped so a short photoperiod cannot produce a
        # ramp longer than the window itself
        rise = min(1.0, since_on / ramp_minutes) if since_on >= 0 else 0.0
        fall = min(1.0, until_off / ramp_minutes) if until_off >= 0 else 0.0
        return brightness * min(rise, fall)

    # ------------------------------------------------------------------ control

    def update(self, now: Optional[dt.datetime] = None) -> float:
        now = now or dt.datetime.now()
        duty = self.override if self.override is not None else self.scheduled_duty(now)
        self.ch.set(duty)
        return duty

    def set_override(self, duty: Optional[float]) -> None:
        """duty of None hands control back to the schedule."""
        self.override = None if duty is None else max(0.0, min(1.0, float(duty)))

    def state(self) -> dict:
        return {
            "duty": round(self.ch.duty, 3),
            "on": self.ch.duty > 0,
            "mode": "manual" if self.override is not None else "schedule",
            "on_hour": LIGHT_ON_HOUR,
            "off_hour": LIGHT_OFF_HOUR,
        }

    def close(self) -> None:
        self.ch.close()
