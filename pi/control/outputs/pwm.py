"""One switched channel, one of the four load mosfets on the hat.

All four loads hang off the hat's 12 V rail: the led strip on gpio 18 with pwm, the
three dosing pumps on 17 / 27 / 22 as plain on/off. The pi only ever drives a gate;
the 12 V never touches it.

Everything is low side switched, so `on` means the mosfet pulls the load's negative
to ground. gpiozero's lgpio backend times the pwm in the kernel, so 1 kHz on the
strip is steady without pigpiod.
"""

from __future__ import annotations

import time


class Channel:
    """A pwm-capable output. In simulate mode it just remembers its state."""

    def __init__(self, pin: int, name: str, frequency: int = 1000,
                 simulate: bool = False) -> None:
        self.pin = pin
        self.name = name
        self.simulate = simulate
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
        """Full on for a fixed time, then off. Returns the seconds actually run.

        The `finally` matters more than anything else in this file. If the process
        is interrupted mid-pulse the pump still stops, because the alternative is a
        peristaltic pump running unattended into a tank.
        """
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
