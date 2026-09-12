"""DS18B20 water temperature, 1-Wire.

The kernel does the bus work. Enable it with `dtoverlay=w1-gpio` in
/boot/firmware/config.txt, then each probe shows up as a directory under
/sys/bus/w1/devices/28-xxxxxxxxxxxx/ with a w1_slave file in it.

The file looks like:
    a2 01 4b 46 7f ff 0c 10 07 : crc=07 YES
    a2 01 4b 46 7f ff 0c 10 07 t=26125

First line ends YES only when the CRC passed. Second line carries millidegrees.
"""

from __future__ import annotations

import glob
import os
import random
from typing import List

from ..hardware import W1_DIR, W1_PREFIX
from ..reading import Reading
from .base import Sensor, checked


class WaterTemp(Sensor):
    name = "water/temp"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._path: str | None = None
        self._sim_c = 21.0
        if not simulate:
            self._path = self._find_probe()

    @staticmethod
    def _find_probe() -> str | None:
        matches = sorted(glob.glob(os.path.join(W1_DIR, W1_PREFIX + "*", "w1_slave")))
        return matches[0] if matches else None

    def read(self) -> List[Reading]:
        if self.simulate:
            # nutrient solution drifts slowly, and tracks room temp
            self._sim_c += random.uniform(-0.08, 0.08)
            self._sim_c = max(18.0, min(26.0, self._sim_c))
            return [checked(self.name, round(self._sim_c, 2), "C")]

        if self._path is None:
            # try again, the probe may have been plugged in since startup
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
            # 85 C is the DS18B20 power-on default. It means the conversion did not
            # run, usually a parasitic power or pull-up problem. It is not a reading.
            return [Reading.bad(self.name, "C", "85C power-on default, conversion did not run", 85.0)]

        return [checked(self.name, milli / 1000.0, "C")]
