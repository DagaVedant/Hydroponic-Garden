"""pH and EC, both analog, both read through the ADS1115 at 0x48.

The pi has no analog input at all, so without this chip neither probe exists.

The important part is the power switching. Two powered electrodes sitting in the
same tank leak current through the solution and corrupt each other's readings. The
hat carries a mosfet on each probe's supply so the loop is:

    ph on, ec off  -> settle -> sample a0 -> ph off
    ec on, ph off  -> settle -> sample a1 -> ec off
    both off

Settle time matters. An electrode that has just been powered needs its reference to
stabilise; sampling immediately gives a number that drifts for the first second or
two. Start at 2s and measure it on the bench.
"""

from __future__ import annotations

import random
import time
from typing import List, Optional

from ..config import (PH_CAL_V4, PH_CAL_V7, PROBE_SETTLE_S, TDS_TEMP_COEFF,
                      TDS_TO_EC)
from ..hardware import (ADS1115_ADDR, ADS_CH_EC, ADS_CH_PH, ADS_FSR_VOLTS, I2C_BUS,
                        PH_CAL_4, PH_CAL_7, PIN_EC_POWER, PIN_PH_POWER)
from ..reading import Reading
from .base import Sensor, checked

REG_CONVERSION = 0x00
REG_CONFIG = 0x01

# single shot, +/-4.096V, 128 SPS, comparator disabled
_MUX = {0: 0b100, 1: 0b101, 2: 0b110, 3: 0b111}
_PGA_4V096 = 0b001
_DR_128SPS = 0b100


def _config_word(channel: int) -> int:
    return (0x8000                      # start a single conversion
            | (_MUX[channel] << 12)
            | (_PGA_4V096 << 9)
            | (1 << 8)                  # single shot mode
            | (_DR_128SPS << 5)
            | 0x03)                     # comparator off


def ph_from_volts(v: float) -> float:
    """Two point calibration. Slope comes from the 4.00 and 7.00 buffers.

    A glass electrode is linear in millivolts against pH, so two points define it.
    The slope is negative on most boards, which is why it is derived rather than
    assumed.
    """
    slope = (PH_CAL_7 - PH_CAL_4) / (PH_CAL_V7 - PH_CAL_V4)
    return PH_CAL_7 + (v - PH_CAL_V7) * slope


def tds_from_volts(v: float, water_temp_c: float) -> float:
    """DFRobot Gravity TDS, ppm, with temperature compensation.

    A DC excitation probe reads higher in warm water because ion mobility rises, so
    the raw voltage has to be normalised back to 25 C before the cubic is applied.
    """
    comp = v / (1.0 + TDS_TEMP_COEFF * (water_temp_c - 25.0))
    return (133.42 * comp ** 3 - 255.86 * comp ** 2 + 857.39 * comp) * 0.5


class ProbePair(Sensor):
    """Both probes, read one at a time so they cannot interfere.

    Set `water_temp_c` before calling read(). EC without temperature compensation
    is not a measurement, so if it is missing the ec reading is marked invalid
    rather than silently wrong.
    """

    name = "probes"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self.water_temp_c: Optional[float] = None
        self._bus = None
        self._ph_power = None
        self._ec_power = None
        self._sim_ph = 6.2
        self._sim_ec = 1150.0

        if not simulate:
            import smbus2
            from gpiozero import DigitalOutputDevice

            self._bus = smbus2.SMBus(I2C_BUS)
            # initial_value False so both probes come up unpowered
            self._ph_power = DigitalOutputDevice(PIN_PH_POWER, initial_value=False)
            self._ec_power = DigitalOutputDevice(PIN_EC_POWER, initial_value=False)

    # ------------------------------------------------------------------ adc

    def _read_volts(self, channel: int) -> Optional[float]:
        cfg = _config_word(channel)
        try:
            self._bus.write_i2c_block_data(ADS1115_ADDR, REG_CONFIG,
                                           [(cfg >> 8) & 0xFF, cfg & 0xFF])
            time.sleep(1.0 / 128 + 0.002)          # one conversion at 128 SPS
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

    # ------------------------------------------------------------------ simulation

    def simulate_dose(self, channel: str, ml: float) -> None:
        """Move the fake tank the way a real dose would.

        Only used in simulate mode. It is what lets the dosing state machine be
        tested against a tank that actually responds, instead of one that ignores
        everything and always looks like a failed dose.
        """
        if not self.simulate:
            return
        from ..config import EC_PER_ML, PH_PER_ML_DOWN
        if channel in EC_PER_ML:
            self._sim_ec += ml * EC_PER_ML[channel]
        elif channel == "ph_down":
            self._sim_ph -= ml * PH_PER_ML_DOWN

    # ------------------------------------------------------------------ read

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
            out.append(checked("water/ph", round(ph_from_volts(v_ph), 2), "pH",
                               note=f"{v_ph:.4f}V"))

        v_ec = self._sample(self._ec_power, ADS_CH_EC)
        if v_ec is None:
            out.append(Reading.bad("water/ec", "uS/cm", "ads1115 read failed"))
        elif self.water_temp_c is None:
            out.append(Reading.bad("water/ec", "uS/cm",
                                   "no water temp, cannot compensate"))
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
