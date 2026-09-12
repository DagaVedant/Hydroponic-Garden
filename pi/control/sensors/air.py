"""SHT31 air temperature and humidity, I2C at 0x44.

One transaction gives both values, so they share a timestamp. 0x24 0x00 is the
high repeatability measurement without clock stretching; it needs up to 15ms
before the result can be read.

Response is 6 bytes: T_msb T_lsb T_crc RH_msb RH_lsb RH_crc.
    T  = -45 + 175 * ticks / 65535
    RH = 100 * ticks / 65535, clamped to 0..100

The part fitted is an SHT31, not the SHT41 the BOM used to name. The two are not
drop-in: an SHT4x takes a one-byte 0xFD and offsets RH by -6, and sending that to
an SHT31 returns nothing rather than failing loudly. Temperature happens to share
a formula, so a wrong driver here reads plausibly and lies about humidity.
"""

from __future__ import annotations

import random
import time
from typing import List

from ..hardware import I2C_BUS, SHT3X_ADDR
from ..reading import Reading
from .base import Sensor, checked, crc8_sensirion

CMD_MEASURE_HIGH_REPEATABILITY = (0x24, 0x00)
MEASURE_DELAY_S = 0.016


class AirSensor(Sensor):
    name = "air"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._bus = None
        self._sim_t = 22.0
        self._sim_rh = 55.0
        if not simulate:
            import smbus2  # noqa: F401  (pi only, absent on a dev machine)
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
            self._bus.i2c_rdwr(smbus2.i2c_msg.write(
                SHT3X_ADDR, list(CMD_MEASURE_HIGH_REPEATABILITY)))
            time.sleep(MEASURE_DELAY_S)
            rx = smbus2.i2c_msg.read(SHT3X_ADDR, 6)
            self._bus.i2c_rdwr(rx)
            data = bytes(rx)
        except OSError as exc:
            note = f"i2c failed: {exc}"
            return [Reading.bad("air/temp", "C", note),
                    Reading.bad("air/humidity", "%RH", note)]

        if len(data) != 6:
            note = f"short read, {len(data)} bytes"
            return [Reading.bad("air/temp", "C", note),
                    Reading.bad("air/humidity", "%RH", note)]

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
