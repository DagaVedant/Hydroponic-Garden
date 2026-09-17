"""The two wire link to the pumps board.

HB (gpio 18) carries 9600 baud command frames to the pico four times a second; the
same edges keep the board's hardware watchdog retriggered, so a crashed pi means a
disarmed rail. FLT (gpio 17) carries telemetry back, and is held low by the pico
for a hard fault. Both are bit-banged with pigpio (pigpiod must be running).

    >P1=0.00,P2=0.00,P3=0.00,L=0.62,F=0.50,A=1*5B     pi -> pico
    <I1=0.000,I2=0.000,I3=0.000,IL=2.35,V=12.1,FL=12.3,SN=345,HB=1,AR=1,E=00*3C   back
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional

PIN_TX = 18
PIN_RX = 17
BAUD = 9600
FRAME_PERIOD_S = 0.25
STALE_AFTER_S = 1.5

FAULT_BITS = {0x01: "rail low", 0x02: "pump over current", 0x04: "led over current",
              0x08: "pump stall", 0x10: "no frames"}


def checksum(body: str) -> int:
    x = 0
    for ch in body.encode("ascii"):
        x ^= ch
    return x


def encode(pumps, led: float, fan: float, arm: bool) -> bytes:
    body = "P1=%.2f,P2=%.2f,P3=%.2f,L=%.2f,F=%.2f,A=%d" % (pumps[0], pumps[1], pumps[2], led, fan, 1 if arm else 0)
    return (">%s*%02X\n" % (body, checksum(body))).encode("ascii")


def decode(line: str) -> Optional[Dict[str, float]]:
    if not line.startswith("<") or "*" not in line:
        return None
    body, _, chk = line[1:].rpartition("*")
    try:
        if checksum(body) != int(chk.strip(), 16):
            return None
        out: Dict[str, float] = {}
        for tok in body.split(","):
            k, _, v = tok.partition("=")
            out[k] = int(v, 16) if k == "E" else float(v)
        return out
    except ValueError:
        return None


class Link:
    """Setpoints go in, telemetry comes out, a thread does the wire."""

    def __init__(self, simulate: bool = False) -> None:
        self.simulate = simulate
        self.pumps = [0.0, 0.0, 0.0]
        self.led = 0.0
        self.fan = 0.0
        self.arm = True
        self.telemetry: Dict[str, float] = {}
        self.telemetry_at = 0.0
        self.fault_line = False
        self._pi = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if not simulate:
            import pigpio
            self._pi = pigpio.pi()
            if not self._pi.connected:
                raise OSError("pigpiod is not running (sudo systemctl enable --now pigpiod)")
            self._pi.set_mode(PIN_TX, pigpio.OUTPUT)
            self._pi.write(PIN_TX, 1)
            self._pi.set_mode(PIN_RX, pigpio.INPUT)
            self._pi.set_pull_up_down(PIN_RX, pigpio.PUD_UP)
            try:
                self._pi.bb_serial_read_close(PIN_RX)
            except pigpio.error:
                pass
            self._pi.bb_serial_read_open(PIN_RX, BAUD)
            self._pi.wave_clear()
        self._thread = threading.Thread(target=self._run, name="link", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ setpoints
    def set_pump(self, index: int, duty: float) -> None:
        with self._lock:
            self.pumps[index] = max(0.0, min(1.0, float(duty)))

    def set_led(self, duty: float) -> None:
        with self._lock:
            self.led = max(0.0, min(1.0, float(duty)))

    def set_fan(self, duty: float) -> None:
        with self._lock:
            self.fan = max(0.0, min(1.0, float(duty)))

    # ------------------------------------------------------------ state
    @property
    def fresh(self) -> bool:
        return time.time() - self.telemetry_at < STALE_AFTER_S

    def faults(self) -> list:
        bits = int(self.telemetry.get("E", 0))
        return [name for bit, name in FAULT_BITS.items() if bits & bit]

    def state(self) -> dict:
        t = self.telemetry
        return {"fresh": self.fresh, "fault_line": self.fault_line, "faults": self.faults(),
                "armed": bool(t.get("AR", 0)), "hb_alive": bool(t.get("HB", 0)),
                "rail_v": t.get("V"), "led_a": t.get("IL"),
                "pump_a": [t.get("I1"), t.get("I2"), t.get("I3")],
                "flow_hz": t.get("FL"), "sonar_mm": t.get("SN"),
                "setpoints": {"pumps": list(self.pumps), "led": self.led, "fan": self.fan}}

    # ------------------------------------------------------------ the wire
    def _run(self) -> None:
        partial = b""
        sim_flow = 0.0
        while not self._stop.is_set():
            with self._lock:
                frame = encode(self.pumps, self.led, self.fan, self.arm)
                pumps, led = list(self.pumps), self.led
            if self._pi is None:
                # a plausible board: currents follow the setpoints, the tank sits at 300 mm
                sim_flow = 12.0 if led > 0 else 0.0
                self.telemetry = {"I1": 0.32 * pumps[0], "I2": 0.31 * pumps[1], "I3": 0.30 * pumps[2],
                                  "IL": 4.4 * led, "V": 12.1, "FL": sim_flow, "SN": 250, "HB": 1, "AR": 1, "E": 0}
                self.telemetry_at = time.time()
                self.fault_line = False
                time.sleep(FRAME_PERIOD_S)
                continue
            try:
                self._pi.wave_clear()
                self._pi.wave_add_serial(PIN_TX, BAUD, frame)
                wid = self._pi.wave_create()
                self._pi.wave_send_once(wid)
                while self._pi.wave_tx_busy():
                    time.sleep(0.005)
                self._pi.wave_delete(wid)
                count, data = self._pi.bb_serial_read(PIN_RX)
                if count > 0:
                    partial += bytes(data)
                    while b"\n" in partial:
                        line, partial = partial.split(b"\n", 1)
                        got = decode(line.decode("ascii", "replace").strip())
                        if got:
                            self.telemetry = got
                            self.telemetry_at = time.time()
                    partial = partial[-256:]
                # the fault line: held low with no traffic for over a second
                self.fault_line = (self._pi.read(PIN_RX) == 0) and not self.fresh
            except Exception as exc:   # pigpio hiccups should not kill the control loop
                print(f"  link: {exc}")
            time.sleep(FRAME_PERIOD_S)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        if self._pi is not None:
            try:
                self._pi.wave_clear()
                self._pi.wave_add_serial(PIN_TX, BAUD, encode([0, 0, 0], 0.0, 0.0, False))
                wid = self._pi.wave_create()
                self._pi.wave_send_once(wid)
                time.sleep(0.1)
                self._pi.wave_delete(wid)
                self._pi.bb_serial_read_close(PIN_RX)
            finally:
                self._pi.stop()


class LinkChannel:
    """Looks like control.Channel, drives one output on the pumps board."""

    def __init__(self, link: Link, kind: str, index: int, name: str) -> None:
        self.link, self.kind, self.index, self.name = link, kind, index, name
        self._duty = 0.0

    @property
    def duty(self) -> float:
        return self._duty

    def set(self, duty: float) -> None:
        self._duty = max(0.0, min(1.0, float(duty)))
        if self.kind == "pump":
            self.link.set_pump(self.index, self._duty)
        elif self.kind == "led":
            self.link.set_led(self._duty)
        else:
            self.link.set_fan(self._duty)

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
        self.off()

    def __str__(self) -> str:
        return f"{self.name}={self._duty:.0%}"
