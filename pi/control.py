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


I2C_BUS = 1
ADS1115_ADDR = 0x48
SHT3X_ADDR = 0x44
ADS_CH_PH, ADS_CH_EC = 0, 1
ADS_FSR_VOLTS = 4.096

PIN_PH_POWER = 23
PIN_EC_POWER = 24
PIN_LIGHTS = 18
PIN_DOSE_MICRO = 17
PIN_DOSE_GRO = 27
PIN_DOSE_PH_DOWN = 22
LIGHT_PWM_HZ = 1000

LEVEL_PORT = "/dev/serial0"
LEVEL_BAUD = 9600
LEVEL_TIMEOUT_S = 0.5
LEVEL_SAMPLES = 5
LEVEL_BLIND_ZONE_MM = 200.0
LEVEL_MAX_RANGE_MM = 6000.0
BUCKET_BORE_MM = 290.0
LEVEL_MIN_VALID_MM = SENSOR_HEIGHT_MM - MAX_FILL_DEPTH_MM
LEVEL_MAX_VALID_MM = SENSOR_HEIGHT_MM + 20.0

W1_DIR = "/sys/bus/w1/devices"
W1_PREFIX = "28-"

PH_CAL_7, PH_CAL_4 = 7.00, 4.00

RANGES = {
    "water/level":  (0.0, 20.0),
    "water/temp":   (0.0, 45.0),
    "water/ph":     (0.0, 14.0),
    "water/ec":     (0.0, 5000.0),
    "air/temp":     (-10.0, 60.0),
    "air/humidity": (0.0, 100.0),
}

MQTT_PORT = 1883
MQTT_KEEPALIVE_S = 60
MQTT_CLIENT_ID = "hydro-control"
MQTT_TOPIC_PREFIX = "hydro"
MQTT_QOS = 1

CSV_HEADER = ["ts", "iso", "sensor", "value", "unit", "valid", "note"]


@dataclass(frozen=True)
class Reading:
    sensor: str
    value: float
    unit: str
    valid: bool
    ts: float = field(default_factory=time.time)
    note: str = ""

    @classmethod
    def bad(cls, sensor: str, unit: str, note: str, value: float = float("nan")) -> "Reading":
        return cls(sensor=sensor, value=value, unit=unit, valid=False, note=note)

    def __str__(self) -> str:
        if not self.valid:
            return f"{self.sensor:<14} FAIL  {self.note}"
        return f"{self.sensor:<14} {self.value:>9.2f} {self.unit}"


def checked(sensor: str, value: float, unit: str, note: str = "") -> Reading:
    lo, hi = RANGES[sensor]
    if value != value:
        return Reading.bad(sensor, unit, "nan")
    if not (lo <= value <= hi):
        return Reading(sensor=sensor, value=value, unit=unit, valid=False,
                       note=f"out of range {lo}..{hi}")
    return Reading(sensor=sensor, value=value, unit=unit, valid=True, note=note)


def crc8_sensirion(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class Sensor:
    name = "unnamed"

    def __init__(self, simulate: bool = False) -> None:
        self.simulate = simulate

    def read(self) -> List[Reading]:
        raise NotImplementedError

    def close(self) -> None:
        pass


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
            self._sim_c += random.uniform(-0.08, 0.08)
            self._sim_c = max(18.0, min(26.0, self._sim_c))
            return [checked(self.name, round(self._sim_c, 2), "C")]

        if self._path is None:
            self._path = self._find_probe()
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
            return [Reading.bad(self.name, "C", "85C power-on default, conversion did not run", 85.0)]
        return [checked(self.name, milli / 1000.0, "C")]


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
            self._sim_depth -= random.uniform(0.0, 1.2)
            if self._sim_depth < 60:
                self._sim_depth = MAX_FILL_DEPTH_MM
            return [checked(self.name, round(self.depth_to_litres(self._sim_depth), 2),
                            "L", note=f"distance {SENSOR_HEIGHT_MM - self._sim_depth:.0f}mm")]

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


REG_CONVERSION, REG_CONFIG = 0x00, 0x01
_MUX = {0: 0b100, 1: 0b101, 2: 0b110, 3: 0b111}
_PGA_4V096, _DR_128SPS = 0b001, 0b100


def _config_word(channel: int) -> int:
    return (0x8000 | (_MUX[channel] << 12) | (_PGA_4V096 << 9) | (1 << 8)
            | (_DR_128SPS << 5) | 0x03)


def ph_from_volts(v: float) -> float:
    slope = (PH_CAL_7 - PH_CAL_4) / (PH_CAL_V7 - PH_CAL_V4)
    return PH_CAL_7 + (v - PH_CAL_V7) * slope


def tds_from_volts(v: float, water_temp_c: float) -> float:
    comp = v / (1.0 + TDS_TEMP_COEFF * (water_temp_c - 25.0))
    return (133.42 * comp ** 3 - 255.86 * comp ** 2 + 857.39 * comp) * 0.5


class ProbePair(Sensor):
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
            self._ph_power = DigitalOutputDevice(PIN_PH_POWER, initial_value=False)
            self._ec_power = DigitalOutputDevice(PIN_EC_POWER, initial_value=False)

    def _read_volts(self, channel: int) -> Optional[float]:
        cfg = _config_word(channel)
        try:
            self._bus.write_i2c_block_data(ADS1115_ADDR, REG_CONFIG, [(cfg >> 8) & 0xFF, cfg & 0xFF])
            time.sleep(1.0 / 128 + 0.002)
            raw = self._bus.read_i2c_block_data(ADS1115_ADDR, REG_CONVERSION, 2)
        except OSError:
            return None
        counts = (raw[0] << 8) | raw[1]
        if counts > 0x7FFF:
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
        if not self.simulate:
            return
        if channel in EC_PER_ML:
            self._sim_ec += ml * EC_PER_ML[channel]
        elif channel == "ph_down":
            self._sim_ph -= ml * PH_PER_ML_DOWN

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_ph += random.uniform(-0.04, 0.06)
            self._sim_ec -= random.uniform(0.0, 4.0)
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


class Channel:
    def __init__(self, pin: int, name: str, frequency: int = 1000, simulate: bool = False) -> None:
        self.pin, self.name, self.simulate = pin, name, simulate
        self._duty = 0.0
        self._dev = None
        if not simulate:
            from gpiozero import PWMOutputDevice
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


def _minutes(t: dt.datetime) -> float:
    return t.hour * 60 + t.minute + t.second / 60.0


class Lights:
    def __init__(self, channel: Channel) -> None:
        self.ch = channel
        self.override: Optional[float] = None

    @staticmethod
    def scheduled_duty(now: dt.datetime, on_hour: float = LIGHT_ON_HOUR,
                       off_hour: float = LIGHT_OFF_HOUR, brightness: float = LIGHT_BRIGHTNESS,
                       ramp_minutes: float = LIGHT_RAMP_MINUTES) -> float:
        on_m, off_m = on_hour * 60.0, off_hour * 60.0
        now_m, day = _minutes(now), 24 * 60.0
        if on_m <= off_m:
            lit = on_m <= now_m < off_m
            since_on, until_off = now_m - on_m, off_m - now_m
        else:
            lit = now_m >= on_m or now_m < off_m
            since_on = now_m - on_m if now_m >= on_m else now_m + (day - on_m)
            until_off = off_m - now_m if now_m < off_m else off_m + (day - now_m)
        if not lit:
            return 0.0
        if ramp_minutes <= 0:
            return brightness
        rise = min(1.0, since_on / ramp_minutes) if since_on >= 0 else 0.0
        fall = min(1.0, until_off / ramp_minutes) if until_off >= 0 else 0.0
        return brightness * min(rise, fall)

    def update(self, now: Optional[dt.datetime] = None) -> float:
        now = now or dt.datetime.now()
        duty = self.override if self.override is not None else self.scheduled_duty(now)
        self.ch.set(duty)
        return duty

    def set_override(self, duty: Optional[float]) -> None:
        self.override = None if duty is None else max(0.0, min(1.0, float(duty)))

    def state(self) -> dict:
        return {"duty": round(self.ch.duty, 3), "on": self.ch.duty > 0,
                "mode": "manual" if self.override is not None else "schedule",
                "on_hour": LIGHT_ON_HOUR, "off_hour": LIGHT_OFF_HOUR}

    def close(self) -> None:
        self.ch.close()


IDLE, DOSING, MIXING, VERIFYING, FAULT = "idle", "dosing", "mixing", "verifying", "fault"
MICRO, GRO, PH_DOWN = "micro", "gro", "ph_down"


class Doser:
    def __init__(self, channels: Dict[str, Channel], simulate: bool = False,
                 on_dose: Optional[Callable[[str, float], None]] = None) -> None:
        self.ch = channels
        self.simulate = simulate
        self.on_dose = on_dose
        self.state = IDLE
        self.fault_reason = ""
        self.enabled = DOSING_ENABLED
        self._runtime: Deque[Tuple[float, float]] = collections.deque()
        self._last_dose_ts = 0.0
        self._pending: Optional[dict] = None
        self._mix_until = 0.0
        self._totals: Dict[str, float] = {MICRO: 0.0, GRO: 0.0, PH_DOWN: 0.0}

    def runtime_last_hour(self) -> float:
        cutoff = time.time() - 3600
        while self._runtime and self._runtime[0][0] < cutoff:
            self._runtime.popleft()
        return sum(s for _, s in self._runtime)

    def interlocks(self, ec, ph, level, water_temp) -> Optional[str]:
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
        micro, gro = self._totals[MICRO], self._totals[GRO]
        if micro <= 0:
            return MICRO
        return GRO if (gro / micro) < MICRO_GRO_RATIO else MICRO

    def decide(self, ec: float, ph: float) -> Optional[Tuple[str, float, str, float]]:
        if ec < DOSE_EC_TARGET - DOSE_EC_DEADBAND:
            channel = self._next_nutrient()
            ml = min((DOSE_EC_TARGET - ec) / EC_PER_ML[channel], DOSE_MAX_ML_PER_EVENT)
            return channel, ml, "water/ec", ml * EC_PER_ML[channel]
        if ph > DOSE_PH_TARGET + DOSE_PH_DEADBAND:
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
            ratio = (moved / expected) if expected else 1.0
            self._pending = None
            if ratio < DOSE_VERIFY_FRACTION:
                self.state = FAULT
                self.fault_reason = (f"{p.get('channel')} dose did not land: expected "
                                     f"{expected:+.3g}, saw {moved:+.3g}. empty bottle, "
                                     f"slipped tube or dead pump")
                return {"state": FAULT, "note": self.fault_reason}
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
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if new_file:
                writer.writerow(CSV_HEADER)
            for r in rows:
                iso = dt.datetime.fromtimestamp(r.ts, dt.timezone.utc).isoformat(timespec="seconds")
                writer.writerow([
                    str(int(r.ts)),
                    iso, r.sensor,
                    "" if r.value != r.value else f"{r.value:g}",
                    r.unit, 1 if r.valid else 0, r.note])
        return len(rows)

    @property
    def path(self) -> Optional[str]:
        return self._path


TOPIC_ONLINE = f"{MQTT_TOPIC_PREFIX}/state/online"
TOPIC_FAULT = f"{MQTT_TOPIC_PREFIX}/state/fault"
CMD_PREFIX = f"{MQTT_TOPIC_PREFIX}/cmd/"


def sensor_topic(sensor: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/sensor/{sensor}"


def payload_for(r: Reading) -> str:
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
        self.commands: "queue.Queue[tuple]" = queue.Queue(maxsize=100)
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("  mqtt: paho-mqtt not installed, publishing disabled")
            return
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
        except AttributeError:
            self._client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
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
        except Exception as exc:
            print(f"  mqtt: {exc}, publishing disabled")
            self.enabled = False

    def close(self) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            self._publish(TOPIC_ONLINE, json.dumps({"online": False, "timestamp": int(time.time())}),
                          retain=True)
            time.sleep(0.1)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass

    def _publish(self, topic: str, payload: str, retain: bool = False) -> bool:
        if not self.enabled:
            return False
        try:
            return self._client.publish(topic, payload, qos=MQTT_QOS, retain=retain).rc == 0
        except Exception:
            return False

    def publish(self, readings: Iterable[Reading]) -> int:
        rows = list(readings)
        if not self.enabled:
            return 0
        if not self.connected and not self._warned:
            print("  mqtt: broker unreachable, csv only until it comes back")
            self._warned = True
        elif self.connected:
            self._warned = False
        sent = sum(1 for r in rows if self._publish(sensor_topic(r.sensor), payload_for(r), retain=True))
        failing = sorted(r.sensor for r in rows if not r.valid)
        self._publish(TOPIC_FAULT, json.dumps({"failing": failing, "count": len(failing),
                                               "timestamp": int(time.time())}), retain=True)
        return sent

    def publish_state(self, name: str, body: dict) -> bool:
        payload = dict(body)
        payload.setdefault("timestamp", int(time.time()))
        return self._publish(f"{MQTT_TOPIC_PREFIX}/state/{name}",
                             json.dumps(payload, separators=(",", ":")), retain=True)

    def heartbeat(self) -> None:
        self._publish(TOPIC_ONLINE, json.dumps({"online": True, "timestamp": int(time.time())}),
                      retain=True)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "off"
        return "connected" if self.connected else "retrying"


_running = True


def _stop(_signum, _frame):
    global _running
    _running = False
    print("\nstopping", flush=True)


class SensorSet:
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
            except Exception as exc:
                print(f"  close failed on {s.name}: {exc}", file=sys.stderr)


class Outputs:
    def __init__(self, simulate: bool, probes: ProbePair) -> None:
        self.lights = Lights(Channel(PIN_LIGHTS, "lights", LIGHT_PWM_HZ, simulate))
        pumps = {MICRO: Channel(PIN_DOSE_MICRO, "micro", 100, simulate),
                 GRO: Channel(PIN_DOSE_GRO, "gro", 100, simulate),
                 PH_DOWN: Channel(PIN_DOSE_PH_DOWN, "ph_down", 100, simulate)}
        self.doser = Doser(pumps, simulate, on_dose=probes.simulate_dose if simulate else None)

    def close(self) -> None:
        self.lights.close()
        self.doser.close()


def value_of(readings: List[Reading], sensor: str) -> Optional[float]:
    for r in readings:
        if r.sensor == sensor:
            return r.value if r.valid else None
    return None


def apply_commands(pub: Publisher, outputs: Outputs) -> None:
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

            logger.write(readings)
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
            deadline = started + args.interval
            while _running and time.time() < deadline:
                time.sleep(min(0.5, max(0.0, deadline - time.time())))
    finally:
        if outputs is not None:
            outputs.close()
        sensors.close()
        pub.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
