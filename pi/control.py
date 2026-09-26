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
import threading
import time
from dataclasses import dataclass, field
from typing import (Callable, Deque, Dict, Iterable, List, Optional, Tuple)

from config import (CSV_DIR, DOSE_EC_DEADBAND, FAN_DUTY, DOSE_EC_TARGET, DOSE_FLOW_ML_PER_S,
                    DOSE_MAX_ML_PER_EVENT, DOSE_MAX_ML_PH_EVENT,
                    DOSE_MAX_SECONDS_PER_HOUR, DOSE_MIN_INTERVAL_S, DOSE_MIN_LEVEL_L,
                    DOSE_MIX_WAIT_S, DOSE_PH_DEADBAND, DOSE_PH_TARGET,
                    DOSE_VERIFY_FRACTION, DOSE_VERIFY_MAX_FRACTION, DOSING_CALIBRATED,
                    DOSING_ENABLED, EC_PER_ML, LIGHT_BRIGHTNESS, LIGHT_OFF_HOUR,
                    LIGHT_ON_HOUR, LIGHT_RAMP_MINUTES, MAX_FILL_DEPTH_MM,
                    MICRO_GRO_RATIO, MQTT_HOST, PH_CAL_V4, PH_CAL_V7, PH_PER_ML_DOWN,
                    PROBE_SETTLE_S, SAMPLE_INTERVAL_S, SENSOR_HEIGHT_MM, TDS_TEMP_COEFF,
                    TDS_TO_EC)

# ---------------------------------------------------------------------------
# hardware map, verified against hat.kicad_pcb -- this is not reasoned from
# memory, it's transcribed.
# ---------------------------------------------------------------------------

I2C_BUS = 1

INA226_ADDR = 0x40
PCA9685_ADDR = 0x41
SHT3X_ADDR = 0x44
ADS1115_PH_EC_ADDR = 0x48        # ph (A0), ec (A1), pump3 current (A2)
ADS1115_PUMP12_ADDR = 0x49       # pump1 current (A0), pump2 current (A2)
ADS1115_LED_ADDR = 0x4A          # led current (A0); A2 is genuinely floating, don't read it
SCD40_ADDR = 0x62                # standard address, not re-strapped
VEML7700_ADDR = 0x10             # standard address, not re-strapped
DS3231_ADDR = 0x68                # standard address, RTC not currently used by this loop

ADS_CH_PH, ADS_CH_EC, ADS_CH_PUMP3 = 0, 1, 2
ADS_CH_PUMP1, ADS_CH_PUMP2 = 0, 2
ADS_CH_LED = 0
ADS_FSR_PROBE_V = 4.096          # +-4.096V range, for the analog probe boards
ADS_FSR_SHUNT_V = 0.256          # +-0.256V range, for the low-side current shunts

PUMP_SHUNT_OHMS = {1: 0.1, 2: 0.1, 3: 0.1}     # R13/R16/R19
LED_SHUNT_OHMS = 0.01                          # R22
INA226_SHUNT_OHMS = 0.005                      # R_SHUNT, the 12V input

PIN_PH_POWER = 23           # /PH_EN
PIN_EC_POWER = 24           # /EC_EN
PIN_FLOW = 17               # /FLOW, off the CD40106 schmitt buffer
PIN_FAN_TACH = 27           # /FAN_TACH
PIN_HB_ALIVE = 18           # /HB_ALIVE -- the watchdog retrigger. see Watchdog below
PIN_ARM = 25                # /ARM -- the software's own deliberate arm/disarm

# the CD4538 monostable's timeout, measured off the real board: R1=470k, C2=10uF
# on MONO_RC/MONO_CEXT. CD4538 timeout ~= 0.7 * R * C -- verify on the bench, this
# is a datasheet-formula estimate, not a bench measurement.
WATCHDOG_TIMEOUT_S = 0.7 * 470_000 * 10e-6      # ~3.3s
WATCHDOG_TOGGLE_S = 1.0                          # retrigger well inside the timeout

JSN_SERIAL_PORT = "/dev/serial0"
JSN_BAUD = 9600

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
    "air/co2":      (0.0, 10000.0),
    "air/light":    (0.0, 120000.0),
    "water/flow":   (0.0, 30.0),
    "pump/1/current": (0.0, 1.5),
    "pump/2/current": (0.0, 1.5),
    "pump/3/current": (0.0, 1.5),
    "lights/current": (0.0, 6.0),
    "fan/rpm":      (0.0, 10000.0),
    "board/armed":  (0.0, 1.0),
    "power/volts":  (9.0, 15.0),
    "power/current": (0.0, 10.0),
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
    """Shared by every Sensirion part on this board: SHT31 and SCD40 both use it."""
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
    """SHT31 at 0x44. temp + humidity."""
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


CMD_SCD40_START_PERIODIC = (0x21, 0xB1)
CMD_SCD40_READ_MEASUREMENT = (0xEC, 0x05)
CMD_SCD40_DATA_READY = (0xE4, 0xB8)


class Co2Sensor(Sensor):
    """SCD40 at 0x62. co2 + temp + humidity, but only co2 is used from here --
    air temp/humidity already come from the SHT31, which settles faster."""
    name = "air/co2"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._bus = None
        self._sim_co2 = 650.0
        if not simulate:
            import smbus2
            self._bus = smbus2.SMBus(I2C_BUS)
            try:
                self._bus.i2c_rdwr(smbus2.i2c_msg.write(SCD40_ADDR, list(CMD_SCD40_START_PERIODIC)))
            except OSError:
                pass  # first read() will report the failure

    def _write_read(self, cmd: Tuple[int, int], n: int, delay_s: float):
        import smbus2
        self._bus.i2c_rdwr(smbus2.i2c_msg.write(SCD40_ADDR, list(cmd)))
        time.sleep(delay_s)
        rx = smbus2.i2c_msg.read(SCD40_ADDR, n)
        self._bus.i2c_rdwr(rx)
        return bytes(rx)

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_co2 += random.uniform(-15.0, 25.0)
            self._sim_co2 = max(420.0, min(1800.0, self._sim_co2))
            return [checked(self.name, round(self._sim_co2, 0), "ppm")]

        try:
            ready = self._write_read(CMD_SCD40_DATA_READY, 3, 0.001)
            if crc8_sensirion(ready[0:2]) != ready[2]:
                return [Reading.bad(self.name, "ppm", "data-ready crc failed")]
            if (((ready[0] << 8) | ready[1]) & 0x07FF) == 0:
                return [Reading.bad(self.name, "ppm", "not ready yet, 5s update cycle")]
            data = self._write_read(CMD_SCD40_READ_MEASUREMENT, 9, 0.001)
        except OSError as exc:
            return [Reading.bad(self.name, "ppm", f"i2c failed: {exc}")]

        if crc8_sensirion(data[0:2]) != data[2]:
            return [Reading.bad(self.name, "ppm", "crc failed")]
        co2 = (data[0] << 8) | data[1]
        return [checked(self.name, float(co2), "ppm")]

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()


VEML_ALS_CONF0 = 0x00
VEML_ALS = 0x04
VEML_LUX_PER_COUNT = 0.0576  # gain=1x, integration=100ms, per Vishay's app note table


class LightSensor(Sensor):
    """VEML7700 at 0x10. confirms the LED strip/photoperiod are doing something --
    not a control input, the photoperiod schedule is timer-based (see Lights)."""
    name = "air/light"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._bus = None
        self._sim_lux = 8000.0
        if not simulate:
            import smbus2
            self._bus = smbus2.SMBus(I2C_BUS)
            try:
                # gain=1x, IT=100ms, not shutdown. see VEML_LUX_PER_COUNT's comment.
                self._bus.write_word_data(VEML7700_ADDR, VEML_ALS_CONF0, 0x0000)
            except OSError:
                pass

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_lux += random.uniform(-200.0, 200.0)
            self._sim_lux = max(0.0, min(20000.0, self._sim_lux))
            return [checked(self.name, round(self._sim_lux, 0), "lux")]
        try:
            raw = self._bus.read_word_data(VEML7700_ADDR, VEML_ALS)
        except OSError as exc:
            return [Reading.bad(self.name, "lux", f"i2c failed: {exc}")]
        return [checked(self.name, round(raw * VEML_LUX_PER_COUNT, 0), "lux")]

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()


BORE_AREA_MM2 = math.pi * (BUCKET_BORE_MM / 2.0) ** 2


class TankLevel(Sensor):
    """JSN-SR04T, wired directly in its own native UART mode (9600 8N1): a 4-byte
    frame, 0xFF header + distance high byte + distance low byte + checksum, sent
    continuously on its own. No more relaying through a pumps board -- this reads
    the sensor's wire protocol directly."""
    name = "water/level"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._ser = None
        self._sim_distance = SENSOR_HEIGHT_MM - 80.0
        if not simulate:
            import serial
            self._ser = serial.Serial(JSN_SERIAL_PORT, JSN_BAUD, timeout=0.3)

    @staticmethod
    def depth_to_litres(depth_mm: float) -> float:
        return depth_mm * BORE_AREA_MM2 / 1_000_000.0

    def _read_distance_mm(self) -> Optional[float]:
        # hunt for the 0xFF header rather than assuming byte alignment -- the
        # sensor free-runs, so a stale partial frame can be sitting in the buffer
        for _ in range(4):
            header = self._ser.read(1)
            if not header:
                return None
            if header[0] != 0xFF:
                continue
            rest = self._ser.read(3)
            if len(rest) != 3:
                return None
            data_h, data_l, checksum = rest
            if (0xFF + data_h + data_l) & 0xFF != checksum:
                continue
            return float((data_h << 8) | data_l)
        return None

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_distance += random.uniform(-1.0, 1.0)
            self._sim_distance = max(LEVEL_MIN_VALID_MM, min(LEVEL_MAX_VALID_MM, self._sim_distance))
            distance = self._sim_distance
        else:
            try:
                distance = self._read_distance_mm()
            except OSError as exc:
                return [Reading.bad(self.name, "L", f"serial failed: {exc}")]
            if distance is None:
                return [Reading.bad(self.name, "L", "no valid frame from the sensor")]

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
        if self._ser is not None:
            self._ser.close()


class PulseCounter:
    """A GPIO edge counter for anything that outputs pulses: the flow sensor and
    the fan tachometer are both this, just with different pulses-per-unit."""

    def __init__(self, pin: int, simulate: bool = False, sim_hz: float = 0.0) -> None:
        self.simulate = simulate
        self._count = 0
        self._lock = threading.Lock()
        self._dev = None
        self._sim_hz = sim_hz
        if not simulate:
            from gpiozero import DigitalInputDevice
            self._dev = DigitalInputDevice(pin, pull_up=False)
            self._dev.when_activated = self._tick

    def _tick(self) -> None:
        with self._lock:
            self._count += 1

    def hz_since_last_call(self) -> float:
        """Call this on a roughly-known interval (the sweep interval); it hands
        back edges-per-second since the last call and resets the counter."""
        if self.simulate:
            return self._sim_hz
        with self._lock:
            n, self._count = self._count, 0
        return n  # caller divides by however many seconds actually elapsed

    def close(self) -> None:
        if self._dev is not None:
            self._dev.close()


class FlowAndFan(Sensor):
    """Flow sensor (BCM17) and fan tach (BCM27). The flow sensor module itself
    isn't sourced yet, so this will report 'no pulses' until one is plugged
    in. That's expected, not a fault."""
    name = "flow_fan"
    FLOW_HZ_PER_LPM = 7.5  # carried over from the old design's flow sensor spec;
                           # re-verify once an actual sensor is sourced and dated

    def __init__(self, simulate: bool = False, interval_s: float = SAMPLE_INTERVAL_S) -> None:
        super().__init__(simulate)
        self.interval_s = interval_s
        self._last_ts = time.time()
        self.flow = PulseCounter(PIN_FLOW, simulate, sim_hz=0.0)
        self.fan = PulseCounter(PIN_FAN_TACH, simulate, sim_hz=(FAN_DUTY * 2400.0 / 60.0 * 2))

    def read(self) -> List[Reading]:
        now = time.time()
        elapsed = max(0.5, now - self._last_ts)
        self._last_ts = now
        flow_hz = self.flow.hz_since_last_call() / elapsed
        fan_hz = self.fan.hz_since_last_call() / elapsed
        out = [checked("water/flow", round(flow_hz / self.FLOW_HZ_PER_LPM, 2), "L/min",
                       note=f"{flow_hz:.1f} Hz" if flow_hz else "no pulses -- sensor not connected yet")]
        # most PC-style fan tachs pulse twice per revolution
        out.append(checked("fan/rpm", round(fan_hz / 2.0 * 60.0, 0), "rpm"))
        return out

    def close(self) -> None:
        self.flow.close()
        self.fan.close()


class Supply(Sensor):
    """The HAT's own 12V input: the INA226 at 0x40. no mains-sense and no
    battery in this design -- both were cut with the UPS."""
    name = "power"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._bus = None
        if not simulate:
            import smbus2
            self._bus = smbus2.SMBus(I2C_BUS)

    def read(self) -> List[Reading]:
        if self.simulate:
            return [checked("power/current", 1.35, "A"), checked("power/volts", 12.05, "V")]
        out: List[Reading] = []
        try:
            raw = self._bus.read_word_data(INA226_ADDR, 0x02)
            bus_v = ((raw & 0xFF) << 8 | raw >> 8) * 1.25e-3
            raw = self._bus.read_word_data(INA226_ADDR, 0x01)
            sv = (raw & 0xFF) << 8 | raw >> 8
            sv = sv - 65536 if sv > 32767 else sv
            out.append(checked("power/volts", round(bus_v, 2), "V"))
            out.append(checked("power/current", round(sv * 2.5e-6 / INA226_SHUNT_OHMS, 3), "A"))
        except OSError as exc:
            out.append(Reading.bad("power/volts", "V", f"ina226: {exc}"))
        return out

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()


REG_CONVERSION, REG_CONFIG = 0x00, 0x01
_MUX = {0: 0b100, 1: 0b101, 2: 0b110, 3: 0b111}   # single-ended vs GND, per channel
_PGA_4V096, _PGA_0V256, _DR_128SPS = 0b001, 0b101, 0b100


def _config_word(channel: int, pga: int) -> int:
    return 0x8000 | (_MUX[channel] << 12) | (pga << 9) | (1 << 8) | (_DR_128SPS << 5) | 0x03


def _read_ads1115_volts(bus, address: int, channel: int, pga: int, fsr: float) -> Optional[float]:
    cfg = _config_word(channel, pga)
    try:
        bus.write_i2c_block_data(address, REG_CONFIG, [(cfg >> 8) & 0xFF, cfg & 0xFF])
        time.sleep(1.0 / 128 + 0.002)
        raw = bus.read_i2c_block_data(address, REG_CONVERSION, 2)
    except OSError:
        return None
    counts = (raw[0] << 8) | raw[1]
    if counts > 0x7FFF:
        counts -= 0x10000
    return counts * fsr / 32768.0


def ph_from_volts(v: float) -> float:
    slope = (PH_CAL_7 - PH_CAL_4) / (PH_CAL_V7 - PH_CAL_V4)
    return PH_CAL_7 + (v - PH_CAL_V7) * slope


def tds_from_volts(v: float, water_temp_c: float) -> float:
    comp = v / (1.0 + TDS_TEMP_COEFF * (water_temp_c - 25.0))
    return (133.42 * comp ** 3 - 255.86 * comp ** 2 + 857.39 * comp) * 0.5


class ProbePair(Sensor):
    """pH and EC, both single-ended off the ADS1115 at 0x48 (channels A0/A1)."""
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

    def _sample(self, power, channel: int) -> Optional[float]:
        power.on()
        try:
            time.sleep(PROBE_SETTLE_S)
            return _read_ads1115_volts(self._bus, ADS1115_PH_EC_ADDR, channel,
                                        _PGA_4V096, ADS_FSR_PROBE_V)
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


class CurrentSense(Sensor):
    """3x pump current + LED current, across the three ADS1115s at 0x48/0x49/0x4A.
    Every one of these is a single-ended read of a low-side shunt: I = V / R."""
    name = "current"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._bus = None
        self._sim = {1: 0.12, 2: 0.12, 3: 0.12, "led": 3.6}
        if not simulate:
            import smbus2
            self._bus = smbus2.SMBus(I2C_BUS)

    def _current(self, address: int, channel: int, shunt_ohms: float) -> Optional[float]:
        v = _read_ads1115_volts(self._bus, address, channel, _PGA_0V256, ADS_FSR_SHUNT_V)
        if v is None:
            return None
        return v / shunt_ohms

    def read(self) -> List[Reading]:
        if self.simulate:
            for k in (1, 2, 3):
                self._sim[k] = max(0.0, self._sim[k] + random.uniform(-0.01, 0.01))
            self._sim["led"] = max(0.0, self._sim["led"] + random.uniform(-0.1, 0.1))
            return [checked("pump/1/current", round(self._sim[1], 3), "A"),
                    checked("pump/2/current", round(self._sim[2], 3), "A"),
                    checked("pump/3/current", round(self._sim[3], 3), "A"),
                    checked("lights/current", round(self._sim["led"], 2), "A")]

        out: List[Reading] = []
        i1 = self._current(ADS1115_PUMP12_ADDR, ADS_CH_PUMP1, PUMP_SHUNT_OHMS[1])
        i2 = self._current(ADS1115_PUMP12_ADDR, ADS_CH_PUMP2, PUMP_SHUNT_OHMS[2])
        i3 = self._current(ADS1115_PH_EC_ADDR, ADS_CH_PUMP3, PUMP_SHUNT_OHMS[3])
        for n, amps in ((1, i1), (2, i2), (3, i3)):
            out.append(Reading.bad(f"pump/{n}/current", "A", "ads1115 read failed") if amps is None
                       else checked(f"pump/{n}/current", round(amps, 3), "A"))
        i_led = self._current(ADS1115_LED_ADDR, ADS_CH_LED, LED_SHUNT_OHMS)
        out.append(Reading.bad("lights/current", "A", "ads1115 read failed") if i_led is None
                   else checked("lights/current", round(i_led, 2), "A"))
        return out

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()


# ---------------------------------------------------------------------------
# PCA9685 -- every PWM output (pumps, LED, fan, the two status LEDs) goes
# through this, not direct Pi GPIO PWM.
# ---------------------------------------------------------------------------

_PCA_MODE1, _PCA_PRESCALE, _PCA_LED0_ON_L = 0x00, 0xFE, 0x06
_PCA_OSC_HZ = 25_000_000


class Pca9685:
    def __init__(self, simulate: bool = False, frequency: float = 1000.0) -> None:
        self.simulate = simulate
        self._bus = None
        if not simulate:
            import smbus2
            self._bus = smbus2.SMBus(I2C_BUS)
            self._set_frequency(frequency)

    def _set_frequency(self, freq: float) -> None:
        prescale = max(3, round(_PCA_OSC_HZ / (4096.0 * freq)) - 1)
        old_mode = self._bus.read_byte_data(PCA9685_ADDR, _PCA_MODE1)
        self._bus.write_byte_data(PCA9685_ADDR, _PCA_MODE1, (old_mode & 0x7F) | 0x10)  # sleep
        self._bus.write_byte_data(PCA9685_ADDR, _PCA_PRESCALE, prescale)
        self._bus.write_byte_data(PCA9685_ADDR, _PCA_MODE1, old_mode)
        time.sleep(0.0005)
        self._bus.write_byte_data(PCA9685_ADDR, _PCA_MODE1, old_mode | 0x80)  # restart, auto-increment

    def set_duty(self, channel: int, duty: float) -> None:
        duty = max(0.0, min(1.0, float(duty)))
        reg = _PCA_LED0_ON_L + 4 * channel
        if self.simulate or self._bus is None:
            return
        if duty <= 0.0:
            self._bus.write_i2c_block_data(PCA9685_ADDR, reg, [0, 0, 0, 0x10])   # full off
            return
        if duty >= 1.0:
            self._bus.write_i2c_block_data(PCA9685_ADDR, reg, [0, 0x10, 0, 0])   # full on
            return
        off = round(duty * 4095)
        self._bus.write_i2c_block_data(PCA9685_ADDR, reg, [0, 0, off & 0xFF, (off >> 8) & 0x0F])

    def close(self) -> None:
        if self._bus is not None:
            for ch in range(16):
                try:
                    self.set_duty(ch, 0.0)
                except OSError:
                    pass
            self._bus.close()


# PCA9685 channel assignment, verified against the schematic
PCA_CH_PUMP1, PCA_CH_PUMP2, PCA_CH_PUMP3 = 0, 1, 2
PCA_CH_LED, PCA_CH_FAN = 3, 4
PCA_CH_STAT_RAIL, PCA_CH_STAT_PUMP = 5, 6


class Channel:
    """One PCA9685 channel, wearing the same interface the old direct-GPIO
    Channel had (on/off/set/pulse) so Lights and Doser don't need to change.

    `simulate=True` (or omitting `pca`) makes this self-contained -- it just
    tracks duty locally without touching any real device, same as the old
    Channel did. That's what the test suite constructs directly."""

    def __init__(self, channel: int, name: str, pca: Optional["Pca9685"] = None,
                 simulate: bool = False) -> None:
        self.pca, self.channel, self.name = pca, channel, name
        self.simulate = simulate or pca is None
        self._duty = 0.0

    @property
    def duty(self) -> float:
        return self._duty

    def set(self, duty: float) -> None:
        duty = max(0.0, min(1.0, float(duty)))
        self._duty = duty
        if not self.simulate:
            self.pca.set_duty(self.channel, duty)

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
        self.set(0.0)

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


class Watchdog:
    """Toggles /HB_ALIVE (BCM18) at WATCHDOG_TOGGLE_S off a dedicated thread, and
    owns /ARM (BCM25). Both must be held for the CD4081 AND gate to enable pumps
    and the LED strip -- if this thread dies, the toggle stops, and the CD4538's
    ~3.3s timeout (measured off the real R1/C2) drops the enable line regardless
    of anything else.

    Deliberately simple: no sensor reads, no I2C, nothing that can block or
    raise for reasons unrelated to "is this process still alive." The point of
    this thread is that it keeps running even when everything else has gone
    wrong."""

    def __init__(self, simulate: bool = False) -> None:
        self.simulate = simulate
        self._hb = self._arm = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if not simulate:
            from gpiozero import DigitalOutputDevice
            self._hb = DigitalOutputDevice(PIN_HB_ALIVE, initial_value=False)
            self._arm = DigitalOutputDevice(PIN_ARM, initial_value=False)  # disarmed until start()

    def start(self) -> None:
        if self.simulate:
            return
        self._thread = threading.Thread(target=self._run, name="watchdog", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._hb.toggle()
            self._stop.wait(WATCHDOG_TOGGLE_S)

    def arm(self) -> None:
        if not self.simulate:
            self._arm.on()

    def disarm(self) -> None:
        if not self.simulate:
            self._arm.off()

    @property
    def armed(self) -> bool:
        return True if self.simulate else bool(self._arm.value)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=WATCHDOG_TOGGLE_S * 2)
        if not self.simulate:
            self._arm.off()
            self._hb.off()
            self._arm.close()
            self._hb.close()


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
        self.co2 = Co2Sensor(simulate)
        self.light = LightSensor(simulate)
        self.level = TankLevel(simulate)
        self.probes = ProbePair(simulate)
        self.current = CurrentSense(simulate)
        self.flow_fan = FlowAndFan(simulate)
        self.supply = Supply(simulate)

    def sweep(self) -> List[Reading]:
        out: List[Reading] = []
        temp = self.water_temp.read()
        out.extend(temp)
        self.probes.water_temp_c = next(
            (r.value for r in temp if r.sensor == "water/temp" and r.valid), None)
        out.extend(self.air.read())
        out.extend(self.co2.read())
        out.extend(self.light.read())
        out.extend(self.level.read())
        out.extend(self.probes.read())
        out.extend(self.current.read())
        out.extend(self.flow_fan.read())
        out.extend(self.supply.read())
        return out

    def close(self) -> None:
        for s in (self.water_temp, self.air, self.co2, self.light, self.level,
                  self.probes, self.current, self.flow_fan, self.supply):
            try:
                s.close()
            except Exception as exc:
                print(f"  close failed on {s.name}: {exc}", file=sys.stderr)


class Outputs:
    """Every PWM output rides the PCA9685 now; nothing is on a second board."""

    def __init__(self, pca: Pca9685, simulate: bool, probes: ProbePair) -> None:
        self.pca = pca
        self.lights = Lights(Channel(PCA_CH_LED, "lights", pca=pca))
        self.fan = Channel(PCA_CH_FAN, "fan", pca=pca)
        self.fan.set(FAN_DUTY)
        pumps = {MICRO: Channel(PCA_CH_PUMP1, "micro", pca=pca),
                 GRO: Channel(PCA_CH_PUMP2, "gro", pca=pca),
                 PH_DOWN: Channel(PCA_CH_PUMP3, "ph_down", pca=pca)}
        self.doser = Doser(pumps, simulate, on_dose=probes.simulate_dose if simulate else None)
        self.stat_rail = Channel(PCA_CH_STAT_RAIL, "stat_rail", pca=pca)
        self.stat_pump = Channel(PCA_CH_STAT_PUMP, "stat_pump", pca=pca)

    def update_status_leds(self, rail_ok: bool) -> None:
        self.stat_rail.set(1.0 if rail_ok else 0.0)
        self.stat_pump.set(1.0 if self.doser.state == FAULT else 0.0)

    def close(self) -> None:
        self.lights.close()
        self.fan.close()
        self.doser.close()
        self.stat_rail.close()
        self.stat_pump.close()


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

    watchdog = None
    try:
        watchdog = Watchdog(args.simulate)
        sensors = SensorSet(args.simulate)
        outputs = None
        if not args.no_outputs:
            pca = Pca9685(args.simulate)
            outputs = Outputs(pca, args.simulate, sensors.probes)
    except ImportError as exc:
        print(f"missing a hardware library: {exc}", file=sys.stderr)
        print("on a dev machine use --simulate", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"hardware init failed: {exc}", file=sys.stderr)
        return 1

    watchdog.start()

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

            rail_ok = value_of(readings, "power/volts") is not None
            line = ""
            if outputs is not None:
                # arm only once a sweep has actually produced a rail reading --
                # never dose/light on the very first, possibly-still-settling pass
                if rail_ok:
                    watchdog.arm()
                else:
                    watchdog.disarm()
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
                outputs.update_status_leds(rail_ok)
                line = f"   lights {duty:.0%}   dosing {result['state']}: {result['note']}"
            pub.publish_state("board", {"armed": watchdog.armed})

            readings.append(checked("board/armed", 1.0 if watchdog.armed else 0.0, ""))
            bad = sum(1 for r in readings if not r.valid)
            print(f"\n[{time.strftime('%H:%M:%S')}] {len(readings)} readings"
                  f"{f', {bad} INVALID' if bad else ''}  -> csv, mqtt {pub.status}, "
                  f"watchdog {'armed' if watchdog.armed else 'DISARMED'}")
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
        watchdog.disarm()
        if outputs is not None:
            outputs.close()
        sensors.close()
        watchdog.close()
        pub.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
