"""JSN-SR04T tank level, UART mode.

Run the module in serial mode (mode 1, the 47k resistor at R27), not trigger/echo.
Timing an echo pulse in microseconds needs a real-time system and Linux is not one,
so trig/echo on a pi gives noisy readings. In serial mode the module does the timing
and hands back a distance.

Protocol: write 0x55, read 4 bytes back.
    0xFF, high, low, checksum      distance_mm = (high << 8) | low
    checksum = (0xFF + high + low) & 0xFF

Geometry, all measured from the bucket floor, numbers from config.py:
    sensor face   SENSOR_HEIGHT_MM, 362 as drawn: in the cap's pod, 6 below the rim
    full tank     MAX_FILL_DEPTH_MM of water, 160, so the sensor reads 202
    empty         the sensor reads 362

The 200mm blind zone is why the fill line is that low. Closer than the blind zone
the module does not report a short distance, it reports garbage, so anything
inside it is marked invalid rather than read as full.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import List, Optional

from ..config import MAX_FILL_DEPTH_MM, SENSOR_HEIGHT_MM
from ..hardware import (BUCKET_BORE_MM, LEVEL_BAUD, LEVEL_BLIND_ZONE_MM,
                        LEVEL_MAX_RANGE_MM, LEVEL_MAX_VALID_MM, LEVEL_MIN_VALID_MM,
                        LEVEL_PORT, LEVEL_SAMPLES, LEVEL_TIMEOUT_S)
from ..reading import Reading
from .base import Sensor, checked

BORE_AREA_MM2 = math.pi * (BUCKET_BORE_MM / 2.0) ** 2
CMD_TRIGGER = b"\x55"


class TankLevel(Sensor):
    name = "water/level"

    def __init__(self, simulate: bool = False) -> None:
        super().__init__(simulate)
        self._port = None
        self._sim_depth = MAX_FILL_DEPTH_MM - 5.0
        if not simulate:
            import serial  # noqa: F401  (pi only)
            self._port = serial.Serial(LEVEL_PORT, LEVEL_BAUD, timeout=LEVEL_TIMEOUT_S)

    # ------------------------------------------------------------------ helpers

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

    # ------------------------------------------------------------------ read

    def read(self) -> List[Reading]:
        if self.simulate:
            self._sim_depth -= random.uniform(0.0, 1.2)      # evaporation and uptake
            if self._sim_depth < 60:
                self._sim_depth = MAX_FILL_DEPTH_MM           # someone topped it up
            return [checked(self.name, round(self.depth_to_litres(self._sim_depth), 2),
                            "L", note=f"distance {SENSOR_HEIGHT_MM - self._sim_depth:.0f}mm")]

        # Median of N. The beam is 75 degrees wide in a 290mm bucket, so it clips the
        # supply pipe and the bucket wall. The water surface is the nearest and
        # flattest reflector so its echo normally wins, but not every single time.
        samples = [s for s in (self._one_sample() for _ in range(LEVEL_SAMPLES))
                   if s is not None]

        if not samples:
            return [Reading.bad(self.name, "L", "no valid frames from sensor")]

        distance = statistics.median(samples)

        if distance < LEVEL_BLIND_ZONE_MM:
            return [Reading.bad(self.name, "L",
                                f"{distance:.0f}mm inside the "
                                f"{LEVEL_BLIND_ZONE_MM:.0f}mm blind zone")]
        if distance > LEVEL_MAX_RANGE_MM:
            return [Reading.bad(self.name, "L", f"{distance:.0f}mm beyond sensor range")]
        if not (LEVEL_MIN_VALID_MM <= distance <= LEVEL_MAX_VALID_MM):
            return [Reading.bad(self.name, "L",
                                f"{distance:.0f}mm outside tank window "
                                f"{LEVEL_MIN_VALID_MM:.0f}..{LEVEL_MAX_VALID_MM:.0f}")]

        depth = SENSOR_HEIGHT_MM - distance
        return [checked(self.name, round(self.depth_to_litres(depth), 2), "L",
                        note=f"distance {distance:.0f}mm")]

    def close(self) -> None:
        if self._port is not None:
            self._port.close()
