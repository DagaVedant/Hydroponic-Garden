"""Append readings to a daily CSV.

Columns are deliberately the same shape as the sqlite table in spec.md:

    ts, sensor, value, unit, valid

plus an `iso` column so the file is readable without converting epochs by hand,
and a `note` column carrying why a reading failed. When the database arrives this
imports with no transformation.

One file per day so a long run does not become one enormous file, and so a bad day
can be inspected on its own.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from typing import Iterable, List

from .config import CSV_DIR
from .hardware import CSV_HEADER
from .reading import Reading


class CsvLogger:
    def __init__(self, directory: str = CSV_DIR) -> None:
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self._current_day: str | None = None
        self._path: str | None = None

    def _path_for(self, ts: float) -> str:
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
        if day != self._current_day:
            self._current_day = day
            self._path = os.path.join(self.directory, f"readings-{day}.csv")
        return self._path

    def write(self, readings: Iterable[Reading]) -> int:
        rows: List[Reading] = list(readings)
        if not rows:
            return 0

        path = self._path_for(rows[0].ts)
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0

        # newline="" is required or csv writes \r\r\n on windows
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if new_file:
                writer.writerow(CSV_HEADER)
            for r in rows:
                iso = dt.datetime.fromtimestamp(r.ts, dt.timezone.utc).isoformat(
                    timespec="seconds")
                # invalid rows are written too. a gap in the data looks identical to
                # "the pi was off"; an explicit valid=0 says the probe was failing
                writer.writerow([
                    str(int(r.ts)),        # truncated, same as the mqtt payload, so
                                           # the (sensor, ts) key dedups both
                    iso,
                    r.sensor,
                    "" if r.value != r.value else f"{r.value:g}",
                    r.unit,
                    1 if r.valid else 0,
                    r.note,
                ])
        return len(rows)

    @property
    def path(self) -> str | None:
        return self._path
