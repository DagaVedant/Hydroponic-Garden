"""The control service, without hardware and without waiting.

    python -m unittest discover -s tests -v       from pi/

The dosing state machine is driven with a fake clock, so five minutes of mixing
takes no time and the whole cycle runs in milliseconds.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                            # noqa: E402
import control                                                           # noqa: E402
from control import (SENSOR_HEIGHT_MM, FAULT, GRO, IDLE, MICRO, MIXING, PH_DOWN, VERIFYING,  # noqa: E402
                     Channel, CsvLogger, Doser, Lights, Reading, TankLevel,
                     checked, crc8_sensirion, payload_for, ph_from_volts,
                     tds_from_volts)


class FakeClock:
    """Stands in for the time module inside control.py."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeTank:
    """Responds to doses the way a mixed tank would, or not at all."""

    def __init__(self, ec: float, ph: float, responds: bool = True, gain: float = 1.0):
        self.ec, self.ph, self.responds, self.gain = ec, ph, responds, gain

    def dose(self, channel: str, ml: float) -> None:
        if not self.responds:
            return
        if channel in config.EC_PER_ML:
            self.ec += ml * config.EC_PER_ML[channel] * self.gain
        elif channel == PH_DOWN:
            self.ph -= ml * config.PH_PER_ML_DOWN * self.gain


def make_doser(tank: FakeTank, clock: FakeClock) -> Doser:
    control.time = clock
    pumps = {k: Channel(0, k, simulate=True) for k in (MICRO, GRO, PH_DOWN)}
    return Doser(pumps, simulate=True, on_dose=tank.dose)


def run_cycle(d: Doser, tank: FakeTank, clock: FakeClock, level: float = 10.0,
              temp: float = 21.0, sweeps: int = 12) -> list:
    """Sweep once a minute until the machine leaves MIXING and VERIFYING."""
    out = []
    for _ in range(sweeps):
        r = d.update(ec=tank.ec, ph=tank.ph, level=level, water_temp=temp)
        out.append(r["state"])
        clock.now += 60
        if r["state"] in (IDLE, FAULT) and len(out) > 1:
            break
    return out


class DosingTests(unittest.TestCase):
    def setUp(self):
        self._real_time = control.time
        self.clock = FakeClock()

    def tearDown(self):
        control.time = self._real_time

    def test_low_ec_doses_micro_first_then_verifies(self):
        tank = FakeTank(ec=1000.0, ph=5.8)
        d = make_doser(tank, self.clock)
        r = d.update(ec=tank.ec, ph=tank.ph, level=10.0, water_temp=21.0)
        self.assertEqual(r["state"], MIXING)
        self.assertEqual(r["channel"], MICRO)
        self.assertLessEqual(r["ml"], config.DOSE_MAX_ML_PER_EVENT)
        self.assertGreater(tank.ec, 1000.0)

        states = run_cycle(d, tank, self.clock)
        self.assertIn(VERIFYING, states)
        self.assertEqual(states[-1], IDLE)
        self.assertEqual(d.state, IDLE)

    def test_dose_that_does_not_land_faults(self):
        tank = FakeTank(ec=1000.0, ph=5.8, responds=False)
        d = make_doser(tank, self.clock)
        states = run_cycle(d, tank, self.clock)
        self.assertEqual(states[-1], FAULT)
        self.assertIn("did not land", d.fault_reason)
        # and stays there: the next sweep is blocked by the fault, not dosing again
        r = d.update(ec=tank.ec, ph=tank.ph, level=10.0, water_temp=21.0)
        self.assertEqual(r["state"], FAULT)
        self.assertIn("in fault", r["note"])
        d.clear_fault()
        self.assertEqual(d.state, IDLE)

    def test_overshoot_faults_too(self):
        tank = FakeTank(ec=1200.0, ph=6.5, gain=10.0)       # a pump that runs 10x long
        d = make_doser(tank, self.clock)
        states = run_cycle(d, tank, self.clock)
        self.assertEqual(states[-1], FAULT)
        self.assertIn("overshot", d.fault_reason)

    def test_ph_high_uses_ph_down_with_its_own_cap(self):
        tank = FakeTank(ec=1200.0, ph=7.0)
        d = make_doser(tank, self.clock)
        r = d.update(ec=tank.ec, ph=tank.ph, level=10.0, water_temp=21.0)
        self.assertEqual(r["channel"], PH_DOWN)
        self.assertLessEqual(r["ml"], config.DOSE_MAX_ML_PH_EVENT)

    def test_ec_is_fixed_before_ph(self):
        tank = FakeTank(ec=1000.0, ph=7.0)
        d = make_doser(tank, self.clock)
        r = d.update(ec=tank.ec, ph=tank.ph, level=10.0, water_temp=21.0)
        self.assertIn(r["channel"], (MICRO, GRO))

    def test_micro_and_gro_alternate(self):
        tank = FakeTank(ec=600.0, ph=5.8)
        d = make_doser(tank, self.clock)
        seen = []
        for _ in range(4):
            r = d.update(ec=tank.ec, ph=tank.ph, level=10.0, water_temp=21.0)
            if r.get("channel"):
                seen.append(r["channel"])
            run_cycle(d, tank, self.clock)
            self.clock.now += config.DOSE_MIN_INTERVAL_S
        self.assertEqual(seen[:2], [MICRO, GRO])

    def test_interlocks(self):
        tank = FakeTank(ec=1000.0, ph=5.8)
        d = make_doser(tank, self.clock)
        self.assertIn("level", d.interlocks(1000.0, 5.8, config.DOSE_MIN_LEVEL_L - 1, 21.0))
        self.assertIn("ec", d.interlocks(None, 5.8, 10.0, 21.0))
        self.assertIn("ph", d.interlocks(1000.0, None, 10.0, 21.0))
        self.assertIn("water temp", d.interlocks(1000.0, 5.8, 10.0, None))
        self.assertIn("level", d.interlocks(1000.0, 5.8, None, 21.0))
        d.enabled = False
        self.assertIn("disabled", d.interlocks(1000.0, 5.8, 10.0, 21.0))
        d.enabled = True
        self.assertIsNone(d.interlocks(1000.0, 5.8, 10.0, 21.0))

    def test_refuses_hardware_until_calibrated(self):
        tank = FakeTank(ec=1000.0, ph=5.8)
        control.time = self.clock
        pumps = {k: Channel(0, k, simulate=True) for k in (MICRO, GRO, PH_DOWN)}
        d = Doser(pumps, simulate=False)
        if config.DOSING_CALIBRATED:
            self.skipTest("config says calibrated")
        self.assertIn("not calibrated", d.interlocks(tank.ec, tank.ph, 10.0, 21.0))

    def test_hourly_cap_and_min_interval(self):
        tank = FakeTank(ec=600.0, ph=5.8)
        d = make_doser(tank, self.clock)
        d.update(ec=tank.ec, ph=tank.ph, level=10.0, water_temp=21.0)
        run_cycle(d, tank, self.clock)
        self.assertIn("too soon", d.interlocks(600.0, 5.8, 10.0, 21.0) or "")
        self.clock.now += config.DOSE_MIN_INTERVAL_S
        d._runtime.append((self.clock.now, config.DOSE_MAX_SECONDS_PER_HOUR))
        self.assertIn("hourly cap", d.interlocks(600.0, 5.8, 10.0, 21.0) or "")
        self.clock.now += 3601
        self.assertIsNone(d.interlocks(600.0, 5.8, 10.0, 21.0))

    def test_sensor_dropping_out_during_verify_faults(self):
        tank = FakeTank(ec=1000.0, ph=5.8)
        d = make_doser(tank, self.clock)
        d.update(ec=tank.ec, ph=tank.ph, level=10.0, water_temp=21.0)
        self.clock.now += config.DOSE_MIX_WAIT_S + 1
        self.assertEqual(d.update(tank.ec, tank.ph, 10.0, 21.0)["state"], VERIFYING)
        self.assertEqual(d.update(None, tank.ph, 10.0, 21.0)["state"], FAULT)


class LightsTests(unittest.TestCase):
    def at(self, h, m=0):
        return dt.datetime(2026, 9, 12, h, m, 0)

    def test_photoperiod(self):
        f = Lights.scheduled_duty
        self.assertEqual(f(self.at(3), 6.0, 22.0, 1.0, 0), 0.0)
        self.assertEqual(f(self.at(12), 6.0, 22.0, 1.0, 0), 1.0)
        self.assertEqual(f(self.at(22), 6.0, 22.0, 1.0, 0), 0.0)
        self.assertEqual(f(self.at(6), 6.0, 22.0, 0.7, 0), 0.7)

    def test_window_across_midnight(self):
        f = Lights.scheduled_duty
        self.assertEqual(f(self.at(23), 20.0, 4.0, 1.0, 0), 1.0)
        self.assertEqual(f(self.at(2), 20.0, 4.0, 1.0, 0), 1.0)
        self.assertEqual(f(self.at(12), 20.0, 4.0, 1.0, 0), 0.0)

    def test_ramp(self):
        f = Lights.scheduled_duty
        self.assertAlmostEqual(f(self.at(6, 5), 6.0, 22.0, 1.0, 10), 0.5)
        self.assertAlmostEqual(f(self.at(21, 55), 6.0, 22.0, 1.0, 10), 0.5)
        self.assertEqual(f(self.at(12), 6.0, 22.0, 1.0, 10), 1.0)

    def test_override(self):
        lights = Lights(Channel(0, "lights", simulate=True))
        lights.set_override(0.25)
        self.assertEqual(lights.update(self.at(3)), 0.25)
        self.assertEqual(lights.state()["mode"], "manual")
        lights.set_override(None)
        self.assertEqual(lights.update(self.at(3)), 0.0)
        self.assertEqual(lights.state()["mode"], "schedule")


class SensorMathTests(unittest.TestCase):
    def test_ph_hits_both_calibration_points(self):
        self.assertAlmostEqual(ph_from_volts(config.PH_CAL_V7), 7.0)
        self.assertAlmostEqual(ph_from_volts(config.PH_CAL_V4), 4.0)

    def test_tds_temperature_compensation_direction(self):
        # warm water reads high, so the compensated number must be lower
        self.assertLess(tds_from_volts(1.5, 30.0), tds_from_volts(1.5, 25.0))
        self.assertGreater(tds_from_volts(1.5, 20.0), tds_from_volts(1.5, 25.0))

    def test_level_geometry(self):
        full = TankLevel.depth_to_litres(config.MAX_FILL_DEPTH_MM)
        self.assertTrue(9.5 < full < 12.0, full)
        # a full tank must still be outside the blind zone, or the sensor is useless
        # exactly when it matters
        self.assertGreater(control.LEVEL_MIN_VALID_MM, control.LEVEL_BLIND_ZONE_MM)
        self.assertLessEqual(config.BANDS["water/level"][1], full + 0.5)

    def test_checked_marks_out_of_range(self):
        self.assertTrue(checked("water/ph", 6.0, "pH").valid)
        self.assertFalse(checked("water/ph", 15.0, "pH").valid)
        self.assertFalse(checked("water/ph", float("nan"), "pH").valid)

    def test_sensirion_crc(self):
        # the example from the sht3x datasheet: 0xBEEF -> 0x92
        self.assertEqual(crc8_sensirion(bytes([0xBE, 0xEF])), 0x92)


class LoggingTests(unittest.TestCase):
    def test_csv_and_mqtt_agree_on_the_timestamp(self):
        """The (sensor, ts) key only dedups a backfilled row against a live one
        if the csv and the payload carry the same integer."""
        r = Reading("water/temp", 20.5, "C", True, ts=1_700_000_000.7)
        with tempfile.TemporaryDirectory() as d:
            CsvLogger(d).write([r])
            with open(os.path.join(d, os.listdir(d)[0]), newline="") as fh:
                row = list(csv.DictReader(fh))[0]
        self.assertEqual(row["ts"], "1700000000")
        self.assertEqual(json.loads(payload_for(r))["timestamp"], 1700000000)
        self.assertEqual(row["iso"], "2023-11-14T22:13:20+00:00")

    def test_invalid_reading_is_written_with_its_note(self):
        r = Reading.bad("water/ph", "pH", "ads1115 read failed")
        with tempfile.TemporaryDirectory() as d:
            CsvLogger(d).write([r])
            text = open(os.path.join(d, os.listdir(d)[0])).read()
        self.assertIn(",water/ph,,pH,0,ads1115 read failed", text)
        body = json.loads(payload_for(r))
        self.assertIsNone(body["value"])
        self.assertFalse(body["valid"])
        self.assertEqual(body["note"], "ads1115 read failed")

    def test_pulse_always_switches_off(self):
        ch = Channel(0, "pump", simulate=True)
        self.assertGreater(ch.pulse(0.01), 0.0)
        self.assertEqual(ch.duty, 0.0)

        # interrupted mid pulse, the pump still stops. this is the one line of
        # pwm.py that matters
        real_sleep = control.time.sleep

        def interrupted(_s):
            raise KeyboardInterrupt

        control.time.sleep = interrupted
        try:
            with self.assertRaises(KeyboardInterrupt):
                ch.pulse(5.0)
        finally:
            control.time.sleep = real_sleep
        self.assertEqual(ch.duty, 0.0)


if __name__ == "__main__":
    unittest.main()
