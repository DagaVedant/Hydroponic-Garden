"""One sensor reading.

Every sensor returns this shape and nothing else. The rule from the spec is that a
reading is never handed on without `valid`, because a failed probe reads as a
plausible number and a plausible wrong number is worse than a gap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


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
        """A reading that failed. Recorded, not dropped.

        A gap in the data is indistinguishable from 'the pi was off'. An explicit
        valid=0 row says the probe was failing, which is the thing worth knowing
        six weeks later.
        """
        return cls(sensor=sensor, value=value, unit=unit, valid=False, note=note)

    def __str__(self) -> str:
        if not self.valid:
            return f"{self.sensor:<14} FAIL  {self.note}"
        return f"{self.sensor:<14} {self.value:>9.2f} {self.unit}"
