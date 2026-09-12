"""The control loop. Reads the hat, logs, publishes, drives the lights and dosing.

    python control.py --simulate            no hardware, fake but plausible
    python control.py                       real hardware on the pi
    python control.py --once                one sweep and exit
    python control.py --interval 10         override the sample period
    python control.py --no-outputs          sense only, drive nothing
    python control.py --no-mqtt             csv only, do not publish

One file, top to bottom: the constants the hat fixes, the Reading type, the four
sensor drivers, the three outputs, the csv log, the mqtt publisher, the loop.
Everything you might want to change is in config.py, not here.

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
import collections
import csv
import datetime as dt
import glob
import json
import math
import os
import queue
import random
import signal
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import (Callable, Deque, Dict, Iterable, List, Optional, Tuple)

from config import (CSV_DIR, DOSE_EC_DEADBAND, DOSE_EC_TARGET, DOSE_FLOW_ML_PER_S,
                    DOSE_MAX_ML_PER_EVENT, DOSE_MAX_ML_PH_EVENT,
                    DOSE_MAX_SECONDS_PER_HOUR, DOSE_MIN_INTERVAL_S, DOSE_MIN_LEVEL_L,
                    DOSE_MIX_WAIT_S, DOSE_PH_DEADBAND, DOSE_PH_TARGET,
                    DOSE_VERIFY_FRACTION, DOSE_VERIFY_MAX_FRACTION, DOSING_CALIBRATED,
                    DOSING_ENABLED, EC_PER_ML, LIGHT_BRIGHTNESS, LIGHT_OFF_HOUR,
                    LIGHT_ON_HOUR, LIGHT_RAMP_MINUTES, MAX_FILL_DEPTH_MM,
                    MICRO_GRO_RATIO, MQTT_HOST, PH_CAL_V4, PH_CAL_V7, PH_PER_ML_DOWN,
                    PROBE_SETTLE_S, SAMPLE_INTERVAL_S, SENSOR_HEIGHT_MM, TDS_TEMP_COEFF,
                    TDS_TO_EC)

# =============================================================================
# what the hat fixes. nothing here is a preference: the pin map is soldered, the
# addresses are strapped on the boards, the blind zone is physics. PCB/README.md
# is the other copy of the pin map and the two must agree.
# =============================================================================

I2C_BUS = 1                 # /dev/i2c-1 on a pi 4b
ADS1115_ADDR = 0x48         # addr pin to gnd. ph on a0, ec on a1
SHT3X_ADDR = 0x44           # sht31, addr low
ADS_CH_PH, ADS_CH_EC = 0, 1
ADS_FSR_VOLTS = 4.096       # pga setting, +/- 4.096 V

PIN_PH_POWER = 23           # pulls the ph board's high side switch on
PIN_EC_POWER = 24           # same for the ec board. only one is ever on
PIN_LIGHTS = 18             # led mosfet, hardware pwm0
PIN_DOSE_MICRO = 17         # dosing ch2, FloraMicro
PIN_DOSE_GRO = 27           # dosing ch3, FloraGro
PIN_DOSE_PH_DOWN = 22       # dosing ch4, pH Down
LIGHT_PWM_HZ = 1000

LEVEL_PORT = "/dev/serial0"
LEVEL_BAUD = 9600
LEVEL_TIMEOUT_S = 0.5
LEVEL_SAMPLES = 5           # median of N. a cheap ultrasonic in a narrow bucket
                            # throws the occasional false echo off the pipe or wall
LEVEL_BLIND_ZONE_MM = 200.0 # closer than this the JSN-SR04T reports garbage, not a distance
LEVEL_MAX_RANGE_MM = 6000.0
BUCKET_BORE_MM = 290.0      # inner diameter
# plausible window for this tank, from the two numbers in config.py. full is only
# just past the blind zone: overfilling reads as "too close", which is invalid
LEVEL_MIN_VALID_MM = SENSOR_HEIGHT_MM - MAX_FILL_DEPTH_MM
LEVEL_MAX_VALID_MM = SENSOR_HEIGHT_MM + 20.0

W1_DIR = "/sys/bus/w1/devices"
W1_PREFIX = "28-"           # ds18b20 family code

PH_CAL_7, PH_CAL_4 = 7.00, 4.00

# outside these a reading is marked invalid. these are "the sensor is broken or
# unplugged" bounds. the "plants are unhappy" bounds are BANDS in config.py
RANGES = {
    "water/level":  (0.0, 20.0),      # litres
    "water/temp":   (0.0, 45.0),      # degrees C
    "water/ph":     (0.0, 14.0),
    "water/ec":     (0.0, 5000.0),    # uS/cm
    "air/temp":     (-10.0, 60.0),
    "air/humidity": (0.0, 100.0),
}

MQTT_PORT = 1883
MQTT_KEEPALIVE_S = 60       # the broker fires our last will about 1.5x this after we die
MQTT_CLIENT_ID = "hydro-control"
MQTT_TOPIC_PREFIX = "hydro"
MQTT_QOS = 1                # at least once. cheap over loopback

CSV_HEADER = ["ts", "iso", "sensor", "value", "unit", "valid", "note"]


# =============================================================================
# one reading. every sensor returns this shape and nothing else. a reading is
# never handed on without `valid`, because a failed probe reads as a plausible
# number and a plausible wrong number is worse than a gap.
# =============================================================================

@dataclass(frozen=True)
class Reading:
    sensor: str          # matches the mqtt topic suffix, e.g. "water/temp"
    value: float         # always populated, even when invalid, so the failure is inspectable
    unit: str
    valid: bool
    ts: float = field(default_factory=time.time)   # unix epoch seconds, utc
    note: str = ""       # why it is invalid, blank when it is fine

    @classmethod
    def bad(cls, sensor: str, unit: str, note: str, value: float = float("nan")) -> "Reading":
        """A reading that failed. Recorded, not dropped: a gap in the data looks the
        same as 'the pi was off', an explicit valid=0 row says the probe was failing."""
        return cls(sensor=sensor, value=value, unit=unit, valid=False, note=note)

    def __str__(self) -> str:
        if not self.valid:
            return f"{self.sensor:<14} FAIL  {self.note}"
        return f"{self.sensor:<14} {self.value:>9.2f} {self.unit}"


def checked(sensor: str, value: float, unit: str, note: str = "") -> Reading:
    """Build a Reading, invalid if it falls outside the plausible range. Catches an
    unplugged probe on a floating pin, or an adc returning rail voltage."""
    lo, hi = RANGES[sensor]
    if value != value:                      # nan
        return Reading.bad(sensor, unit, "nan")
    if not (lo <= value <= hi):
        return Reading(sensor=sensor, value=value, unit=unit, valid=False,
                       note=f"out of range {lo}..{hi}")
    return Reading(sensor=sensor, value=value, unit=unit, valid=True, note=note)


def crc8_sensirion(data: bytes) -> int:
    """CRC-8, polynomial 0x31, init 0xFF. Without it a corrupted i2c read produces
    a temperature that looks fine."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class Sensor:
    """One interface for all of them. read() returns a list because the sht31
    hands back temperature and humidity from a single transaction."""

    name = "unnamed"

    def __init__(self, simulate: bool = False) -> None:
        self.simulate = simulate

    def read(self) -> List[Reading]:
        raise NotImplementedError

    def close(self) -> None:
        pass


# =============================================================================
# ds18b20 water temperature over 1-wire. the kernel does the bus work: enable
# `dtoverlay=w1-gpio`, and each probe is a directory under /sys/bus/w1/devices
# with a w1_slave file whose first line ends YES when the crc passed and whose
# second carries t=<millidegrees>.
# =============================================================================

class WaterTemp(Sensor):
    name = "water/temp"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._path: Optional[str] = None
        self._sim_c = 21.0
        if not simulate:
            self._path = self._find_probe()

    @staticmethod
    def _find_probe() -> Optional[str]:
        matches = sorted(glob.glob(os.path.join(W1_DIR, W1_PREFIX + "*", "w1_slave")))
        return matches[0] if matches else None

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_c += random.uniform(-0.08, 0.08)      # tracks room temp, slowly
            self._sim_c = max(18.0, min(26.0, self._sim_c))
            return [checked(self.name, round(self._sim_c, 2), "C")]

        if self._path is None:
            self._path = self._find_probe()                 # plugged in since startup?
            if self._path is None:
                return [Reading.bad(self.name, "C", "no 1-wire device found")]
        try:
            with open(self._path, "r") as fh:
                lines = fh.read().splitlines()
        except OSError as exc:
            self._path = None
            return [Reading.bad(self.name, "C", f"read failed: {exc}")]

        if len(lines) < 2 or not lines[0].strip().endswith("YES"):
            return [Reading.bad(self.name, "C", "1-wire crc failed")]
        marker = lines[1].find("t=")
        if marker < 0:
            return [Reading.bad(self.name, "C", "no t= field")]
        milli = int(lines[1][marker + 2:])
        if milli == 85000:
            # the power-on default. the conversion did not run, usually a parasitic
            # power or pull-up problem. it is not a reading
            return [Reading.bad(self.name, "C", "85C power-on default, conversion did not run", 85.0)]
        return [checked(self.name, milli / 1000.0, "C")]


# =============================================================================
# sht31 air temperature and humidity, i2c at 0x44. 0x24 0x00 is the high
# repeatability measurement without clock stretching, ready after 15 ms; the
# response is T_msb T_lsb T_crc RH_msb RH_lsb RH_crc. it is an sht31, not the
# sht41 the bom once named: an sht4x takes a one byte 0xFD and offsets RH, and
# sending that to an sht31 returns nothing rather than failing loudly.
# =============================================================================

CMD_MEASURE_HIGH_REPEATABILITY = (0x24, 0x00)
MEASURE_DELAY_S = 0.016


class AirSensor(Sensor):
    name = "air"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._bus = None
        self._sim_t, self._sim_rh = 22.0, 55.0
        if not simulate:
            import smbus2
            self._bus = smbus2.SMBus(I2C_BUS)

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_t += random.uniform(-0.15, 0.15)
            self._sim_rh += random.uniform(-0.6, 0.6)
            self._sim_t = max(18.0, min(28.0, self._sim_t))
            self._sim_rh = max(35.0, min(85.0, self._sim_rh))
            return [checked("air/temp", round(self._sim_t, 2), "C"),
                    checked("air/humidity", round(self._sim_rh, 1), "%RH")]

        import smbus2
        try:
            self._bus.i2c_rdwr(smbus2.i2c_msg.write(SHT3X_ADDR, list(CMD_MEASURE_HIGH_REPEATABILITY)))
            time.sleep(MEASURE_DELAY_S)
            rx = smbus2.i2c_msg.read(SHT3X_ADDR, 6)
            self._bus.i2c_rdwr(rx)
            data = bytes(rx)
        except OSError as exc:
            note = f"i2c failed: {exc}"
            return [Reading.bad("air/temp", "C", note), Reading.bad("air/humidity", "%RH", note)]
        if len(data) != 6:
            note = f"short read, {len(data)} bytes"
            return [Reading.bad("air/temp", "C", note), Reading.bad("air/humidity", "%RH", note)]

        out: List[Reading] = []
        if crc8_sensirion(data[0:2]) != data[2]:
            out.append(Reading.bad("air/temp", "C", "crc failed"))
        else:
            ticks = (data[0] << 8) | data[1]
            out.append(checked("air/temp", round(-45.0 + 175.0 * ticks / 65535.0, 2), "C"))
        if crc8_sensirion(data[3:5]) != data[5]:
            out.append(Reading.bad("air/humidity", "%RH", "crc failed"))
        else:
            ticks = (data[3] << 8) | data[4]
            rh = 100.0 * ticks / 65535.0
            out.append(checked("air/humidity", round(max(0.0, min(100.0, rh)), 1), "%RH"))
        return out

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()


# =============================================================================
# jsn-sr04t tank level, uart mode (mode 1, the 47k at R27), not trigger/echo:
# timing an echo pulse in microseconds needs a real time system and linux is
# not one. write 0x55, read 0xFF hi lo sum; distance_mm = hi << 8 | lo.
#
# geometry, from the bucket floor, numbers in config.py: the sensor face is
# SENSOR_HEIGHT_MM up (362 as drawn: on the pod lip in the cap, 6 below the
# rim), a full tank is MAX_FILL_DEPTH_MM of water (160) so it reads 202, and
# empty reads 362. the 200 mm blind zone is why the fill line is that low.
# =============================================================================

BORE_AREA_MM2 = math.pi * (BUCKET_BORE_MM / 2.0) ** 2
CMD_TRIGGER = b"\x55"


class TankLevel(Sensor):
    name = "water/level"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._port = None
        self._sim_depth = MAX_FILL_DEPTH_MM - 5.0
        if not simulate:
            import serial
            self._port = serial.Serial(LEVEL_PORT, LEVEL_BAUD, timeout=LEVEL_TIMEOUT_S)

    @staticmethod
    def depth_to_litres(depth_mm: float) -> float:
        return depth_mm * BORE_AREA_MM2 / 1_000_000.0

    def _one_sample(self) -> Optional[float]:
        """One distance in mm, or None if the frame was bad."""
        try:
            self._port.reset_input_buffer()
            self._port.write(CMD_TRIGGER)
            frame = self._port.read(4)
        except OSError:
            return None
        if len(frame) != 4 or frame[0] != 0xFF:
            return None
        if (0xFF + frame[1] + frame[2]) & 0xFF != frame[3]:
            return None
        return float((frame[1] << 8) | frame[2])

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_depth -= random.uniform(0.0, 1.2)      # evaporation and uptake
            if self._sim_depth < 60:
                self._sim_depth = MAX_FILL_DEPTH_MM           # someone topped it up
            return [checked(self.name, round(self.depth_to_litres(self._sim_depth), 2),
                            "L", note=f"distance {SENSOR_HEIGHT_MM - self._sim_depth:.0f}mm")]

        # median of N. the beam is 75 degrees wide in a 290 mm bucket, so it clips
        # the pipe and the wall. the water surface is the nearest and flattest
        # reflector so its echo normally wins, but not every single time
        samples = [s for s in (self._one_sample() for _ in range(LEVEL_SAMPLES)) if s is not None]
        if not samples:
            return [Reading.bad(self.name, "L", "no valid frames from sensor")]
        distance = statistics.median(samples)

        if distance < LEVEL_BLIND_ZONE_MM:
            return [Reading.bad(self.name, "L",
                                f"{distance:.0f}mm inside the {LEVEL_BLIND_ZONE_MM:.0f}mm blind zone")]
        if distance > LEVEL_MAX_RANGE_MM:
            return [Reading.bad(self.name, "L", f"{distance:.0f}mm beyond sensor range")]
        if not (LEVEL_MIN_VALID_MM <= distance <= LEVEL_MAX_VALID_MM):
            return [Reading.bad(self.name, "L", f"{distance:.0f}mm outside tank window "
                                f"{LEVEL_MIN_VALID_MM:.0f}..{LEVEL_MAX_VALID_MM:.0f}")]
        depth = SENSOR_HEIGHT_MM - distance
        return [checked(self.name, round(self.depth_to_litres(depth), 2), "L",
                        note=f"distance {distance:.0f}mm")]

    def close(self) -> None:
        if self._port is not None:
            self._port.close()


# =============================================================================
# ph and ec, both analog, both through the ads1115 at 0x48. the pi has no
# analog input at all. the important part is the power switching: two powered
# electrodes in the same tank leak current through the solution and corrupt
# each other, so the hat has a mosfet on each probe's supply and the loop is
# ph on -> settle -> sample a0 -> ph off, then the same for ec, then both off.
# =============================================================================

REG_CONVERSION, REG_CONFIG = 0x00, 0x01
_MUX = {0: 0b100, 1: 0b101, 2: 0b110, 3: 0b111}
_PGA_4V096, _DR_128SPS = 0b001, 0b100


def _config_word(channel: int) -> int:
    """single shot, +/-4.096 V, 128 sps, comparator off"""
    return (0x8000 | (_MUX[channel] << 12) | (_PGA_4V096 << 9) | (1 << 8)
            | (_DR_128SPS << 5) | 0x03)


def ph_from_volts(v: float) -> float:
    """Two point calibration through the 4.00 and 7.00 buffers. a glass electrode
    is linear in millivolts against ph, and the slope is negative on most boards,
    which is why it is derived rather than assumed."""
    slope = (PH_CAL_7 - PH_CAL_4) / (PH_CAL_V7 - PH_CAL_V4)
    return PH_CAL_7 + (v - PH_CAL_V7) * slope


def tds_from_volts(v: float, water_temp_c: float) -> float:
    """DFRobot Gravity TDS, ppm. a dc excitation probe reads higher in warm water,
    so the voltage is normalised back to 25 C before the cubic is applied."""
    comp = v / (1.0 + TDS_TEMP_COEFF * (water_temp_c - 25.0))
    return (133.42 * comp ** 3 - 255.86 * comp ** 2 + 857.39 * comp) * 0.5


class ProbePair(Sensor):
    """Both probes, one at a time. set `water_temp_c` before read(): ec without
    temperature compensation is not a measurement, so if it is missing the ec
    reading is marked invalid rather than silently wrong."""

    name = "probes"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self.water_temp_c: Optional[float] = None
        self._bus = self._ph_power = self._ec_power = None
        self._sim_ph, self._sim_ec = 6.2, 1150.0
        if not simulate:
            import smbus2
            from gpiozero import DigitalOutputDevice
            self._bus = smbus2.SMBus(I2C_BUS)
            # initial_value False so both probes come up unpowered
            self._ph_power = DigitalOutputDevice(PIN_PH_POWER, initial_value=False)
            self._ec_power = DigitalOutputDevice(PIN_EC_POWER, initial_value=False)

    def _read_volts(self, channel: int) -> Optional[float]:
        cfg = _config_word(channel)
        try:
            self._bus.write_i2c_block_data(ADS1115_ADDR, REG_CONFIG, [(cfg >> 8) & 0xFF, cfg & 0xFF])
            time.sleep(1.0 / 128 + 0.002)          # one conversion at 128 sps
            raw = self._bus.read_i2c_block_data(ADS1115_ADDR, REG_CONVERSION, 2)
        except OSError:
            return None
        counts = (raw[0] << 8) | raw[1]
        if counts > 0x7FFF:                        # 16 bit two's complement
            counts -= 0x10000
        return counts * ADS_FSR_VOLTS / 32768.0

    def _sample(self, power, channel: int) -> Optional[float]:
        power.on()
        try:
            time.sleep(PROBE_SETTLE_S)
            return self._read_volts(channel)
        finally:
            power.off()

    def simulate_dose(self, channel: str, ml: float) -> None:
        """Move the fake tank the way a real dose would, so the dosing state machine
        can be watched converging instead of dosing into a void."""
        if not self.simulate:
            return
        if channel in EC_PER_ML:
            self._sim_ec += ml * EC_PER_ML[channel]
        elif channel == "ph_down":
            self._sim_ph -= ml * PH_PER_ML_DOWN

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_ph += random.uniform(-0.04, 0.06)     # drifts up as plants feed
            self._sim_ec -= random.uniform(0.0, 4.0)        # drops as they eat
            self._sim_ph = max(5.2, min(7.6, self._sim_ph))
            self._sim_ec = max(600.0, min(2200.0, self._sim_ec))
            return [checked("water/ph", round(self._sim_ph, 2), "pH"),
                    checked("water/ec", round(self._sim_ec, 0), "uS/cm")]

        out: List[Reading] = []
        v_ph = self._sample(self._ph_power, ADS_CH_PH)
        if v_ph is None:
            out.append(Reading.bad("water/ph", "pH", "ads1115 read failed"))
        else:
            out.append(checked("water/ph", round(ph_from_volts(v_ph), 2), "pH", note=f"{v_ph:.4f}V"))

        v_ec = self._sample(self._ec_power, ADS_CH_EC)
        if v_ec is None:
            out.append(Reading.bad("water/ec", "uS/cm", "ads1115 read failed"))
        elif self.water_temp_c is None:
            out.append(Reading.bad("water/ec", "uS/cm", "no water temp, cannot compensate"))
        else:
            ppm = tds_from_volts(v_ec, self.water_temp_c)
            out.append(checked("water/ec", round(ppm * TDS_TO_EC, 0), "uS/cm",
                               note=f"{v_ec:.4f}V at {self.water_temp_c:.1f}C"))
        return out

    def close(self) -> None:
        for pin in (self._ph_power, self._ec_power):
            if pin is not None:
                pin.off()
                pin.close()
        if self._bus is not None:
            self._bus.close()


# =============================================================================
# one switched channel, one of the four load mosfets on the hat. all four hang
# off the 12 V rail: the strip on gpio 18 with pwm, the pumps on 17 / 27 / 22 as
# plain on/off. low side switched, so `on` pulls the load's negative to ground.
# gpiozero's lgpio backend times the pwm in the kernel, so 1 kHz is steady.
# =============================================================================

class Channel:
    def __init__(self, pin: int, name: str, frequency: int = 1000, simulate: bool = False) -> None:
        self.pin, self.name, self.simulate = pin, name, simulate
        self._duty = 0.0
        self._dev = None
        if not simulate:
            from gpiozero import PWMOutputDevice
            # initial_value 0 so nothing is energised at import time. a board that
            # comes up with the pumps running is a board that empties a bottle
            self._dev = PWMOutputDevice(pin, frequency=frequency, initial_value=0.0)

    @property
    def duty(self) -> float:
        return self._duty

    def set(self, duty: float) -> None:
        duty = max(0.0, min(1.0, float(duty)))
        self._duty = duty
        if self._dev is not None:
            self._dev.value = duty

    def on(self) -> None:
        self.set(1.0)

    def off(self) -> None:
        self.set(0.0)

    def pulse(self, seconds: float) -> float:
        """Full on for a fixed time, then off. the `finally` matters more than
        anything else in this file: interrupted mid pulse, the pump still stops."""
        if seconds <= 0:
            return 0.0
        started = time.time()
        try:
            self.on()
            time.sleep(seconds)
        finally:
            self.off()
        return time.time() - started

    def close(self) -> None:
        if self._dev is not None:
            self._dev.value = 0.0
            self._dev.close()

    def __str__(self) -> str:
        return f"{self.name}={self._duty:.0%}"


# =============================================================================
# photoperiod and dimming for the strip. on at LIGHT_ON_HOUR, off at
# LIGHT_OFF_HOUR (the window may cross midnight), a ramp at each end so 54 W of
# led does not snap on at 6am, and a manual override over hydro/cmd/lights.
# =============================================================================

def _minutes(t: dt.datetime) -> float:
    return t.hour * 60 + t.minute + t.second / 60.0


class Lights:
    def __init__(self, channel: Channel) -> None:
        self.ch = channel
        self.override: Optional[float] = None      # None means follow the schedule

    @staticmethod
    def scheduled_duty(now: dt.datetime, on_hour: float = LIGHT_ON_HOUR,
                       off_hour: float = LIGHT_OFF_HOUR, brightness: float = LIGHT_BRIGHTNESS,
                       ramp_minutes: float = LIGHT_RAMP_MINUTES) -> float:
        """What the duty should be right now, 0.0 to 1.0. a pure function of the
        clock so it can be tested at any hour without waiting for it."""
        on_m, off_m = on_hour * 60.0, off_hour * 60.0
        now_m, day = _minutes(now), 24 * 60.0
        if on_m <= off_m:
            lit = on_m <= now_m < off_m
            since_on, until_off = now_m - on_m, off_m - now_m
        else:                                       # wraps midnight, e.g. on 20:00 off 12:00
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

    def update(self, now: Optional[dt.datetime] = None) -> float:
        now = now or dt.datetime.now()
        duty = self.override if self.override is not None else self.scheduled_duty(now)
        self.ch.set(duty)
        return duty

    def set_override(self, duty: Optional[float]) -> None:
        """duty of None hands control back to the schedule."""
        self.override = None if duty is None else max(0.0, min(1.0, float(duty)))

    def state(self) -> dict:
        return {"duty": round(self.ch.duty, 3), "on": self.ch.duty > 0,
                "mode": "manual" if self.override is not None else "schedule",
                "on_hour": LIGHT_ON_HOUR, "off_hour": LIGHT_OFF_HOUR}

    def close(self) -> None:
        self.ch.close()


# =============================================================================
# nutrient and ph dosing. the only code in the project that can destroy a tank:
# a stuck pump empties a bottle of ph down into 10 L of solution in an
# afternoon. so it will not dose real chemicals using guessed numbers
# (DOSING_CALIBRATED gates the hardware), it never runs two channels in one
# event (micro and gro together precipitate calcium phosphate), it fixes ec
# before ph (nutrients move ph), and every dose is verified:
#
#     IDLE --(out of band)--> DOSING --> MIXING --> VERIFYING --> IDLE
#                                                       |
#                                        (did not move, or moved too far) --> FAULT
#
# FAULT is sticky and needs a human. a dose that does not show up means an empty
# bottle, a slipped tube or a dead pump, and the response is to stop, not to
# dose harder. this is what replaced the float switches.
# =============================================================================

IDLE, DOSING, MIXING, VERIFYING, FAULT = "idle", "dosing", "mixing", "verifying", "fault"
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

    def runtime_last_hour(self) -> float:
        cutoff = time.time() - 3600
        while self._runtime and self._runtime[0][0] < cutoff:
            self._runtime.popleft()
        return sum(s for _, s in self._runtime)

    def interlocks(self, ec, ph, level, water_temp) -> Optional[str]:
        """Every reason not to dose. the first one, or None to proceed."""
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

    def _next_nutrient(self) -> str:
        """Which nutrient part is furthest behind its ratio. micro goes in first."""
        micro, gro = self._totals[MICRO], self._totals[GRO]
        if micro <= 0:
            return MICRO
        return GRO if (gro / micro) < MICRO_GRO_RATIO else MICRO

    def decide(self, ec: float, ph: float) -> Optional[Tuple[str, float, str, float]]:
        """(channel, ml, the sensor that should move, by how much), or None."""
        if ec < DOSE_EC_TARGET - DOSE_EC_DEADBAND:
            channel = self._next_nutrient()
            ml = min((DOSE_EC_TARGET - ec) / EC_PER_ML[channel], DOSE_MAX_ML_PER_EVENT)
            return channel, ml, "water/ec", ml * EC_PER_ML[channel]
        if ph > DOSE_PH_TARGET + DOSE_PH_DEADBAND:
            # its own cap: overshooting ph is easy and hard to walk back
            ml = min((ph - DOSE_PH_TARGET) / PH_PER_ML_DOWN, DOSE_MAX_ML_PH_EVENT)
            return PH_DOWN, ml, "water/ph", -(ml * PH_PER_ML_DOWN)
        return None

    def _run_pump(self, channel: str, ml: float) -> float:
        seconds = ml / DOSE_FLOW_ML_PER_S[channel]
        if self.simulate:
            time.sleep(0.01)
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
        """Call once per sweep. returns what happened, for logging and mqtt."""
        now = time.time()

        if self.state == MIXING:
            if now < self._mix_until:
                return {"state": MIXING, "note": f"{self._mix_until - now:.0f}s left"}
            self.state = VERIFYING
            return {"state": VERIFYING, "note": "reading back"}

        if self.state == VERIFYING:
            p = self._pending or {}
            actual_now = ec if p.get("sensor") == "water/ec" else ph
            if actual_now is None:
                self.state, self.fault_reason = FAULT, "sensor invalid during verification"
                return {"state": FAULT, "note": self.fault_reason}
            moved = actual_now - p.get("before", actual_now)
            expected = p.get("expected", 0.0)
            # a ratio, so one test covers both directions: ph down moves the reading
            # down and nutrients move it up, and same-sign numbers divide positive
            ratio = (moved / expected) if expected else 1.0
            self._pending = None
            if ratio < DOSE_VERIFY_FRACTION:
                self.state = FAULT
                self.fault_reason = (f"{p.get('channel')} dose did not land: expected "
                                     f"{expected:+.3g}, saw {moved:+.3g}. empty bottle, "
                                     f"slipped tube or dead pump")
                return {"state": FAULT, "note": self.fault_reason}
            # too much movement is a failed dose as well: a pump that did not stop,
            # or a flow rate measured wrong, and both get worse if the answer is to
            # dose on
            if ratio > DOSE_VERIFY_MAX_FRACTION:
                self.state = FAULT
                self.fault_reason = (f"{p.get('channel')} dose overshot: expected "
                                     f"{expected:+.3g}, saw {moved:+.3g}. the pump did "
                                     f"not stop, or the flow rate is miscalibrated")
                return {"state": FAULT, "note": self.fault_reason}
            self.state = IDLE
            return {"state": IDLE, "note": f"verified, moved {moved:+.3g}"}

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
        self._pending = {"channel": channel, "sensor": sensor, "before": before,
                         "expected": expected, "ml": ml}
        self._mix_until = time.time() + DOSE_MIX_WAIT_S
        self.state = MIXING
        return {"state": MIXING, "channel": channel, "ml": round(ml, 2),
                "seconds": round(seconds, 2), "note": f"dosed {ml:.1f}mL of {channel}, mixing"}

    def clear_fault(self) -> None:
        self.state, self.fault_reason, self._pending = IDLE, "", None

    def state_dict(self) -> dict:
        return {"state": self.state, "enabled": self.enabled, "calibrated": DOSING_CALIBRATED,
                "fault": self.fault_reason,
                "runtime_last_hour_s": round(self.runtime_last_hour(), 1),
                "cap_s": DOSE_MAX_SECONDS_PER_HOUR,
                "totals_ml": {k: round(v, 2) for k, v in self._totals.items()}}

    def close(self) -> None:
        for ch in self.ch.values():
            ch.close()


# =============================================================================
# the daily csv. same columns as the sqlite table so a backfill is a load, not
# a transformation, plus `iso` so the file is readable and `note` so a failed
# row says why. one file per day. the durable record: written first, always.
# =============================================================================

class CsvLogger:
    def __init__(self, directory: str = CSV_DIR) -> None:
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self._current_day: Optional[str] = None
        self._path: Optional[str] = None

    def _path_for(self, ts: float) -> str:
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
        if day != self._current_day:
            self._current_day = day
            self._path = os.path.join(self.directory, f"readings-{day}.csv")
        return self._path

    def write(self, readings: Iterable[Reading]) -> int:
        rows = list(readings)
        if not rows:
            return 0
        path = self._path_for(rows[0].ts)
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as fh:       # newline="" or windows writes \r\r\n
            writer = csv.writer(fh)
            if new_file:
                writer.writerow(CSV_HEADER)
            for r in rows:
                iso = dt.datetime.fromtimestamp(r.ts, dt.timezone.utc).isoformat(timespec="seconds")
                writer.writerow([
                    str(int(r.ts)),        # truncated, same as the mqtt payload, so
                                           # the (sensor, ts) key dedups both
                    iso, r.sensor,
                    "" if r.value != r.value else f"{r.value:g}",
                    r.unit, 1 if r.valid else 0, r.note])
        return len(rows)

    @property
    def path(self) -> Optional[str]:
        return self._path


# =============================================================================
# the mqtt publisher. the broker is on localhost; it is there so the loop, the
# database and the dashboard do not have to know about each other. two rules:
# publishing never breaks logging (no broker, no paho, every call fails quietly),
# and the broker announces our death through a last will, because a heartbeat we
# send ourselves cannot report that we stopped being able to send heartbeats.
# =============================================================================

TOPIC_ONLINE = f"{MQTT_TOPIC_PREFIX}/state/online"
TOPIC_FAULT = f"{MQTT_TOPIC_PREFIX}/state/fault"
CMD_PREFIX = f"{MQTT_TOPIC_PREFIX}/cmd/"


def sensor_topic(sensor: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/sensor/{sensor}"


def payload_for(r: Reading) -> str:
    """value, unit, timestamp, valid, and note when something failed."""
    body = {"value": None if r.value != r.value else round(r.value, 4), "unit": r.unit,
            "timestamp": int(r.ts), "valid": r.valid}
    if r.note:
        body["note"] = r.note
    return json.dumps(body, separators=(",", ":"))


class Publisher:
    def __init__(self, host: str = MQTT_HOST, port: int = MQTT_PORT) -> None:
        self.host, self.port = host, port
        self.enabled = self.connected = False
        self._client = None
        self._warned = False
        # commands arrive on paho's thread but touch gpio, so they are queued and
        # applied by the main loop
        self.commands: "queue.Queue[tuple]" = queue.Queue(maxsize=100)
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("  mqtt: paho-mqtt not installed, publishing disabled")
            return
        try:                                    # paho 2.x wants the callback api version
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
        except AttributeError:
            self._client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        # the will. retained, so a dashboard connecting later sees we are gone
        self._client.will_set(TOPIC_ONLINE, json.dumps({"online": False}), qos=MQTT_QOS, retain=True)
        self.enabled = True

    def _on_connect(self, _client, _userdata, _flags, reason_code, *_args) -> None:
        ok = getattr(reason_code, "is_failure", None)
        self.connected = (not ok()) if callable(ok) else (reason_code == 0)
        if self.connected:
            print(f"  mqtt: connected to {self.host}:{self.port}")
            self.heartbeat()
            self._client.subscribe(CMD_PREFIX + "#", qos=MQTT_QOS)
        else:
            print(f"  mqtt: connect refused, {reason_code}")

    def _on_disconnect(self, _client, _userdata, *args) -> None:
        self.connected = False
        print("  mqtt: disconnected, will retry in the background")

    def _on_message(self, _client, _userdata, msg) -> None:
        """Queue a command. never act on it here; this is paho's thread."""
        if not msg.topic.startswith(CMD_PREFIX):
            return
        try:
            body = json.loads(msg.payload.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            print(f"  mqtt: bad command payload on {msg.topic}")
            return
        if isinstance(body, dict):
            try:
                self.commands.put_nowait((msg.topic[len(CMD_PREFIX):], body))
            except queue.Full:
                pass

    def start(self) -> None:
        if not self.enabled:
            return
        try:
            self._client.connect_async(self.host, self.port, MQTT_KEEPALIVE_S)
            self._client.loop_start()
        except Exception as exc:                              # noqa: BLE001
            print(f"  mqtt: {exc}, publishing disabled")
            self.enabled = False

    def close(self) -> None:
        if not self.enabled or self._client is None:
            return
        try:                                    # say goodbye properly, so the will does not fire
            self._publish(TOPIC_ONLINE, json.dumps({"online": False, "timestamp": int(time.time())}),
                          retain=True)
            time.sleep(0.1)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:                                     # noqa: BLE001
            pass

    def _publish(self, topic: str, payload: str, retain: bool = False) -> bool:
        if not self.enabled:
            return False
        try:
            return self._client.publish(topic, payload, qos=MQTT_QOS, retain=retain).rc == 0
        except Exception:                                     # noqa: BLE001
            return False

    def publish(self, readings: Iterable[Reading]) -> int:
        """Push a sweep, retained, so a dashboard that connects late sees the
        current value of everything instead of an empty screen."""
        rows = list(readings)
        if not self.enabled:
            return 0
        if not self.connected and not self._warned:
            print("  mqtt: broker unreachable, csv only until it comes back")
            self._warned = True
        elif self.connected:
            self._warned = False
        sent = sum(1 for r in rows if self._publish(sensor_topic(r.sensor), payload_for(r), retain=True))
        # the sensors currently failing. empty means healthy. a state, not an event
        failing = sorted(r.sensor for r in rows if not r.valid)
        self._publish(TOPIC_FAULT, json.dumps({"failing": failing, "count": len(failing),
                                               "timestamp": int(time.time())}), retain=True)
        return sent

    def publish_state(self, name: str, body: dict) -> bool:
        """hydro/state/<name>, retained. lights and dosing use this."""
        payload = dict(body)
        payload.setdefault("timestamp", int(time.time()))
        return self._publish(f"{MQTT_TOPIC_PREFIX}/state/{name}",
                             json.dumps(payload, separators=(",", ":")), retain=True)

    def heartbeat(self) -> None:
        """The will covers a dead process. this covers one that is alive but has
        stopped sweeping, which the will cannot see."""
        self._publish(TOPIC_ONLINE, json.dumps({"online": True, "timestamp": int(time.time())}),
                      retain=True)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "off"
        return "connected" if self.connected else "retrying"


# =============================================================================
# the loop
# =============================================================================

_running = True


def _stop(_signum, _frame):
    global _running
    _running = False
    print("\nstopping", flush=True)


class SensorSet:
    """All four devices, in a deliberate order: water temp first because ec needs
    it, the probes last because they are slowest (two settles, about 4 s)."""

    def __init__(self, simulate: bool) -> None:
        self.water_temp = WaterTemp(simulate)
        self.air = AirSensor(simulate)
        self.level = TankLevel(simulate)
        self.probes = ProbePair(simulate)

    def sweep(self) -> List[Reading]:
        out: List[Reading] = []
        temp = self.water_temp.read()
        out.extend(temp)
        self.probes.water_temp_c = next(
            (r.value for r in temp if r.sensor == "water/temp" and r.valid), None)
        out.extend(self.air.read())
        out.extend(self.level.read())
        out.extend(self.probes.read())
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
        pumps = {MICRO: Channel(PIN_DOSE_MICRO, "micro", 100, simulate),
                 GRO: Channel(PIN_DOSE_GRO, "gro", 100, simulate),
                 PH_DOWN: Channel(PIN_DOSE_PH_DOWN, "ph_down", 100, simulate)}
        # in simulation the dose moves the fake tank, so the control law can be
        # watched converging instead of dosing into a void that never responds
        self.doser = Doser(pumps, simulate, on_dose=probes.simulate_dose if simulate else None)

    def close(self) -> None:
        self.lights.close()
        self.doser.close()


def value_of(readings: List[Reading], sensor: str) -> Optional[float]:
    """The value if it was valid, else None. never a number we do not trust."""
    for r in readings:
        if r.sensor == sensor:
            return r.value if r.valid else None
    return None


def apply_commands(pub: Publisher, outputs: Outputs) -> None:
    """Drain queued mqtt commands on the main thread, where gpio is safe."""
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
    ap.add_argument("--simulate", action="store_true", help="no hardware, plausible fake readings")
    ap.add_argument("--once", action="store_true", help="one sweep then exit")
    ap.add_argument("--interval", type=float, default=SAMPLE_INTERVAL_S,
                    help=f"seconds between sweeps (default {SAMPLE_INTERVAL_S:.0f})")
    ap.add_argument("--dir", default=CSV_DIR, help="where to write the csv")
    ap.add_argument("--no-mqtt", action="store_true", help="csv only, do not publish")
    ap.add_argument("--broker", default=MQTT_HOST, help="broker host")
    ap.add_argument("--no-outputs", action="store_true", help="sense only, drive nothing")
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

    logger = CsvLogger(args.dir)
    pub = Publisher(args.broker)
    if args.no_mqtt:
        pub.enabled = False
    pub.start()

    print(f"{'simulate' if args.simulate else 'hardware'} mode, every {args.interval:.0f}s, "
          f"writing to {logger.directory}/")
    if outputs is not None and not args.simulate and not DOSING_CALIBRATED:
        print("  dosing: NOT CALIBRATED, pumps will not run. fill in the MEASURE blocks in "
              "config.py and flip DOSING_CALIBRATED")

    try:
        while _running:
            started = time.time()
            readings = sensors.sweep()

            logger.write(readings)             # csv first, always
            pub.publish(readings)
            pub.heartbeat()

            line = ""
            if outputs is not None:
                apply_commands(pub, outputs)
                duty = outputs.lights.update(dt.datetime.now())
                pub.publish_state("lights", outputs.lights.state())
                result = outputs.doser.update(
                    ec=value_of(readings, "water/ec"), ph=value_of(readings, "water/ph"),
                    level=value_of(readings, "water/level"),
                    water_temp=value_of(readings, "water/temp"))
                state = outputs.doser.state_dict()
                state.update(result)
                pub.publish_state("dosing", state)
                line = f"   lights {duty:.0%}   dosing {result['state']}: {result['note']}"

            bad = sum(1 for r in readings if not r.valid)
            print(f"\n[{time.strftime('%H:%M:%S')}] {len(readings)} readings"
                  f"{f', {bad} INVALID' if bad else ''}  -> csv, mqtt {pub.status}")
            for r in readings:
                print(f"   {r}")
            if line:
                print(line)

            if args.once or not _running:
                break
            # sleep out the rest of the period in slices: python resumes a sleep
            # after a signal handler returns, so one long sleep would make a stop
            # request wait out the whole period and systemd would kill us before
            # the pumps were switched off
            deadline = started + args.interval
            while _running and time.time() < deadline:
                time.sleep(min(0.5, max(0.0, deadline - time.time())))
    finally:
        # outputs go down first. a pump left running is the one failure here that
        # does real damage
        if outputs is not None:
            outputs.close()
        sensors.close()
        pub.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
