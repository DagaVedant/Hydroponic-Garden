"""Shared plumbing for every sensor driver."""

from __future__ import annotations

from typing import List

from ..hardware import RANGES
from ..reading import Reading


class Sensor:
    """One interface for all of them, so main.py does not care what is plugged in.

    read() returns a list because some parts give more than one value. The SHT31
    hands back temperature and humidity from a single I2C transaction, and
    splitting that into two round trips would be dishonest about when each was
    taken.
    """

    name = "unnamed"

    def __init__(self, simulate: bool = False) -> None:
        self.simulate = simulate

    def read(self) -> List[Reading]:
        raise NotImplementedError

    def close(self) -> None:
        pass


def checked(sensor: str, value: float, unit: str, note: str = "") -> Reading:
    """Build a Reading, marking it invalid if it falls outside the plausible range.

    This catches an unplugged probe reading a floating pin, or an ADC returning
    rail voltage, both of which produce a number that looks like data.
    """
    lo, hi = RANGES[sensor]
    if value != value:                      # nan
        return Reading.bad(sensor, unit, "nan")
    if not (lo <= value <= hi):
        return Reading(sensor=sensor, value=value, unit=unit, valid=False,
                       note=f"out of range {lo}..{hi}")
    return Reading(sensor=sensor, value=value, unit=unit, valid=True, note=note)


def crc8_sensirion(data: bytes) -> int:
    """CRC-8, polynomial 0x31, init 0xFF. Sensirion parts checksum every word.

    Without this a corrupted I2C read produces a temperature that looks fine.
    """
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc
