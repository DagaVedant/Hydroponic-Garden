"""The control loop. Reads sensors, logs, publishes, drives the lights and dosing.

    python -m control.main --simulate            no hardware, fake but plausible
    python -m control.main                       real hardware on the pi
    python -m control.main --once                one sweep and exit
    python -m control.main --interval 10         override the sample period
    python -m control.main --no-outputs          sense only, drive nothing

Order inside a sweep is deliberate:

    read -> csv -> publish -> lights -> dosing -> state

The csv is written before anything else can go wrong, the lights are cheap and
never fail, and dosing runs last because it is the only thing here that can do
damage and it wants the freshest possible readings to decide on.

**The pump is not in this list.** It runs continuously off its own gfci outlet and
no code path can stop it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import queue
import signal
import sys
import time
from typing import Dict, List, Optional

from .config import DOSING_CALIBRATED, SAMPLE_INTERVAL_S
from .hardware import (LIGHT_PWM_HZ, PIN_DOSE_GRO, PIN_DOSE_MICRO, PIN_DOSE_PH_DOWN,
                       PIN_LIGHTS)
from .csv_log import CsvLogger
from .mqtt_pub import Publisher
from .outputs.dosing import GRO, MICRO, PH_DOWN, Doser
from .outputs.lights import Lights
from .outputs.pwm import Channel
from .reading import Reading
from .sensors.air import AirSensor
from .sensors.level import TankLevel
from .sensors.probes import ProbePair
from .sensors.water_temp import WaterTemp

_running = True


def _stop(_signum, _frame):
    global _running
    _running = False
    print("\nstopping", flush=True)


class SensorSet:
    """All four devices, read in a deliberate order.

    Water temp comes first because the EC reading needs it. A DC excitation
    conductivity probe reads high in warm water, so without a temperature to
    compensate against the number is not a measurement.
    """

    def __init__(self, simulate: bool) -> None:
        self.water_temp = WaterTemp(simulate)
        self.air = AirSensor(simulate)
        self.level = TankLevel(simulate)
        self.probes = ProbePair(simulate)

    def sweep(self) -> List[Reading]:
        out: List[Reading] = []

        temp = self.water_temp.read()
        out.extend(temp)

        good = next((r.value for r in temp if r.sensor == "water/temp" and r.valid), None)
        self.probes.water_temp_c = good

        out.extend(self.air.read())
        out.extend(self.level.read())
        out.extend(self.probes.read())      # slowest, roughly 4s of settling
        return out

    def close(self) -> None:
        for s in (self.water_temp, self.air, self.level, self.probes):
            try:
                s.close()
            except Exception as exc:                      # noqa: BLE001
                print(f"  close failed on {s.name}: {exc}", file=sys.stderr)


class Outputs:
    """The led strip and the three dosing pumps, the four mosfets on the hat."""

    def __init__(self, simulate: bool, probes: ProbePair) -> None:
        self.lights = Lights(Channel(PIN_LIGHTS, "lights", LIGHT_PWM_HZ, simulate))
        pumps: Dict[str, Channel] = {
            MICRO:   Channel(PIN_DOSE_MICRO, "micro", 100, simulate),
            GRO:     Channel(PIN_DOSE_GRO, "gro", 100, simulate),
            PH_DOWN: Channel(PIN_DOSE_PH_DOWN, "ph_down", 100, simulate),
        }
        # in simulation the dose moves the fake tank, so the control law can be
        # watched converging instead of dosing into a void that never responds
        self.doser = Doser(pumps, simulate,
                           on_dose=probes.simulate_dose if simulate else None)

    def close(self) -> None:
        self.lights.close()
        self.doser.close()


def value_of(readings: List[Reading], sensor: str) -> Optional[float]:
    """The value if it was valid, else None. Never a number we do not trust."""
    for r in readings:
        if r.sensor == sensor:
            return r.value if r.valid else None
    return None


def apply_commands(pub: Publisher, outputs: Outputs) -> None:
    """Drain queued mqtt commands. Runs on the main thread, so gpio is safe."""
    while True:
        try:
            name, body = pub.commands.get_nowait()
        except queue.Empty:
            return

        if name == "lights":
            if body.get("auto"):
                outputs.lights.set_override(None)
                print("  cmd: lights back on schedule")
            elif "duty" in body:
                outputs.lights.set_override(body["duty"])
                print(f"  cmd: lights forced to {body['duty']}")
            elif "on" in body:
                outputs.lights.set_override(1.0 if body["on"] else 0.0)
                print(f"  cmd: lights forced {'on' if body['on'] else 'off'}")

        elif name == "dose":
            if body.get("clear_fault"):
                outputs.doser.clear_fault()
                print("  cmd: dosing fault cleared")
            elif "enabled" in body:
                outputs.doser.enabled = bool(body["enabled"])
                print(f"  cmd: dosing enabled={outputs.doser.enabled}")
            else:
                print(f"  cmd: ignored dose command {body}")


def main() -> int:
    ap = argparse.ArgumentParser(description="hydroponic tower control loop")
    ap.add_argument("--simulate", action="store_true",
                    help="no hardware, generate plausible readings")
    ap.add_argument("--once", action="store_true", help="one sweep then exit")
    ap.add_argument("--interval", type=float, default=SAMPLE_INTERVAL_S,
                    help=f"seconds between sweeps (default {SAMPLE_INTERVAL_S:.0f})")
    ap.add_argument("--dir", default=None, help="where to write the csv")
    ap.add_argument("--no-mqtt", action="store_true", help="csv only, do not publish")
    ap.add_argument("--broker", default=None, help="broker host, default localhost")
    ap.add_argument("--no-outputs", action="store_true",
                    help="sense only, drive neither lights nor pumps")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        sensors = SensorSet(args.simulate)
        outputs = None if args.no_outputs else Outputs(args.simulate, sensors.probes)
    except ImportError as exc:
        print(f"missing a hardware library: {exc}", file=sys.stderr)
        print("on a dev machine use --simulate", file=sys.stderr)
        return 1

    logger = CsvLogger(args.dir) if args.dir else CsvLogger()

    pub = Publisher(args.broker) if args.broker else Publisher()
    if args.no_mqtt:
        pub.enabled = False
    pub.start()

    mode = "simulate" if args.simulate else "hardware"
    print(f"{mode} mode, every {args.interval:.0f}s, writing to {logger.directory}/")
    if outputs is not None and not args.simulate and not DOSING_CALIBRATED:
        print("  dosing: NOT CALIBRATED, pumps will not run. measure the three "
              "MEASURE blocks in config.py and flip DOSING_CALIBRATED")

    try:
        while _running:
            started = time.time()
            readings = sensors.sweep()

            # csv first, always. it is the durable record and it must not depend
            # on anything happening on the network
            logger.write(readings)
            pub.publish(readings)
            pub.heartbeat()

            line = ""
            if outputs is not None:
                apply_commands(pub, outputs)

                duty = outputs.lights.update(dt.datetime.now())
                pub.publish_state("lights", outputs.lights.state())

                result = outputs.doser.update(
                    ec=value_of(readings, "water/ec"),
                    ph=value_of(readings, "water/ph"),
                    level=value_of(readings, "water/level"),
                    water_temp=value_of(readings, "water/temp"),
                )
                state = outputs.doser.state_dict()
                state.update(result)
                pub.publish_state("dosing", state)
                line = (f"   lights {duty:.0%}   "
                        f"dosing {result['state']}: {result['note']}")

            bad = sum(1 for r in readings if not r.valid)
            stamp = time.strftime("%H:%M:%S")
            print(f"\n[{stamp}] {len(readings)} readings"
                  f"{f', {bad} INVALID' if bad else ''}"
                  f"  -> csv, mqtt {pub.status}")
            for r in readings:
                print(f"   {r}")
            if line:
                print(line)

            if args.once or not _running:
                break

            # subtract the time the sweep took, so the period is the period. slept
            # in slices: python resumes a sleep after a signal handler returns, so
            # one long sleep would make a stop request wait out the whole period
            # and systemd would kill us before the pumps were switched off
            deadline = started + args.interval
            while _running and time.time() < deadline:
                time.sleep(min(0.5, max(0.0, deadline - time.time())))
    finally:
        # outputs go down before anything else. a pump left running is the one
        # failure here that does real damage
        if outputs is not None:
            outputs.close()
        sensors.close()
        pub.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
