"""Phone notifications when something needs a human.

    python -m alerts.main                 run it
    python -m alerts.main --dry-run       print what would be sent, send nothing
    python -m alerts.main --test          send one test notification and exit

Nothing switches the pump, so the alerts are the only thing between a problem and
dead plants. This watches the broker and pushes to ntfy (set ALERT_NTFY_TOPIC in
config.py, subscribe to the same topic in the app). With no topic set it prints.

What it watches for:

    level low            top up the tank
    level rose           the pump stopped and the tower drained back into the tank.
                         a heuristic, and a top up trips it too, but it uses a
                         sensor that is already fitted and it catches the failure
                         that kills plants fastest
    dosing fault         a dose did not land, or overshot. empty bottle, slipped
                         tube, dead pump, wrong flow rate. the control service
                         has already stopped dosing; this tells you
    out of band          ph, ec, water temp, air, humidity outside BANDS for a while
    sensor failing       a sensor invalid for a while. one bad sweep is a blip
    control offline      the broker fired the control service's last will, or the
                         heartbeat has stopped

Every condition is a state, not an event: one notification when it starts, a
reminder every ALERT_REPEAT_S while it lasts, one when it clears. A sensor that
flaps does not produce a stream of messages, because the out of band and failing
conditions have to hold for a while before they count.

Same thread rule as ingest: paho's callback only parses and queues, the main loop
does the thinking.
"""

from __future__ import annotations

import argparse
import collections
import json
import queue
import signal
import sys
import time
import urllib.error
import urllib.request
from typing import Deque, Dict, List, Optional, Tuple

sys.path.insert(0, ".")

from control.config import (ALERT_HEARTBEAT_S, ALERT_LEVEL_LOW_L,       # noqa: E402
                            ALERT_LEVEL_RISE_L, ALERT_LEVEL_RISE_WINDOW_S,
                            ALERT_NTFY_SERVER, ALERT_NTFY_TOPIC,
                            ALERT_OUT_OF_BAND_S, ALERT_REPEAT_S,
                            ALERT_SENSOR_FAIL_S, BANDS, MQTT_HOST)
from control.hardware import MQTT_PORT, MQTT_TOPIC_PREFIX                # noqa: E402

SENSOR_PREFIX = f"{MQTT_TOPIC_PREFIX}/sensor/"
STATE_PREFIX = f"{MQTT_TOPIC_PREFIX}/state/"
TOPIC_ALERTS = f"{MQTT_TOPIC_PREFIX}/state/alerts"

LABEL = {"water/level": "water level", "water/ph": "pH", "water/ec": "EC",
         "water/temp": "water temp", "air/temp": "air temp",
         "air/humidity": "humidity"}
UNIT = {"water/level": " L", "water/ph": "", "water/ec": " uS/cm",
        "water/temp": " C", "air/temp": " C", "air/humidity": "%"}

URGENT = ("level_rise", "dosing_fault", "offline", "level_low")
# conditions that are really events. they clear themselves (the level window slides
# past the rise) and "cleared" would read as "fixed", which nobody checked
EVENTS = ("level_rise",)

_running = True


def _stop(_signum, _frame):
    global _running
    _running = False


def _fmt(sensor: str, value: float) -> str:
    if sensor == "water/ec":
        return f"{value:.0f}{UNIT[sensor]}"
    return f"{value:.2f}{UNIT[sensor]}".replace(".00", "")


# ---------------------------------------------------------------- the rules

class Rules:
    """Turns readings and state into a set of active conditions.

    Pure: feed it readings and state with timestamps, call tick(now), get back
    what changed. No clock of its own, so every rule can be tested at any speed.
    """

    def __init__(self) -> None:
        self.last: Dict[str, Tuple[float, float]] = {}          # sensor -> (ts, value)
        self.out_since: Dict[str, float] = {}                   # sensor -> ts it left its band
        self.fail_since: Dict[str, float] = {}                  # sensor -> ts it started failing
        self.level_hist: Deque[Tuple[float, float]] = collections.deque()
        self.dosing_fault = ""
        self.online: Optional[bool] = None
        self.heartbeat_ts: Optional[float] = None
        self.active: Dict[str, dict] = {}                       # key -> {text, since, sent}

    # ---- inputs

    def feed_reading(self, sensor: str, value: Optional[float], valid: bool, ts: float) -> None:
        if not valid or value is None:
            self.fail_since.setdefault(sensor, ts)
            return
        self.fail_since.pop(sensor, None)
        self.last[sensor] = (ts, value)

        band = BANDS.get(sensor)
        if band and not (band[0] <= value <= band[1]):
            self.out_since.setdefault(sensor, ts)
        else:
            self.out_since.pop(sensor, None)

        if sensor == "water/level":
            self.level_hist.append((ts, value))
            cutoff = ts - ALERT_LEVEL_RISE_WINDOW_S
            while self.level_hist and self.level_hist[0][0] < cutoff:
                self.level_hist.popleft()

    def feed_state(self, name: str, body: dict, ts: float) -> None:
        if name == "online":
            self.online = bool(body.get("online"))
            if self.online:
                self.heartbeat_ts = float(body.get("timestamp", ts))
        elif name == "dosing":
            self.dosing_fault = str(body.get("fault") or "") if body.get("state") == "fault" else ""

    # ---- evaluation

    def conditions(self, now: float) -> Dict[str, str]:
        """Every condition that holds right now, key -> message."""
        c: Dict[str, str] = {}

        if self.online is False:
            c["offline"] = "control service is offline. the pi is down or the service crashed"
        elif self.heartbeat_ts is not None and now - self.heartbeat_ts > ALERT_HEARTBEAT_S:
            mins = (now - self.heartbeat_ts) / 60
            c["offline"] = f"no heartbeat from the control service for {mins:.0f} min"

        if self.dosing_fault:
            c["dosing_fault"] = f"dosing stopped: {self.dosing_fault}. clear the fault on the dashboard once fixed"

        lvl = self.last.get("water/level")
        if lvl and lvl[1] < ALERT_LEVEL_LOW_L:
            c["level_low"] = f"tank is down to {lvl[1]:.1f} L, top it up"

        if len(self.level_hist) >= 2:
            low_ts, low = min(self.level_hist, key=lambda p: p[1])
            cur_ts, cur = self.level_hist[-1]
            if cur_ts > low_ts and cur - low >= ALERT_LEVEL_RISE_L:
                mins = max(1, round((cur_ts - low_ts) / 60))
                c["level_rise"] = (f"tank rose {cur - low:.1f} L in {mins} min. if you did not "
                                   f"top it up, the pump has stopped and the tower drained back")

        for sensor, since in self.out_since.items():
            if now - since >= ALERT_OUT_OF_BAND_S and sensor in self.last:
                lo, hi = BANDS[sensor]
                value = self.last[sensor][1]
                mins = (now - since) / 60
                c[f"band:{sensor}"] = (f"{LABEL.get(sensor, sensor)} is {_fmt(sensor, value)}, "
                                       f"outside {_fmt(sensor, lo)} to {_fmt(sensor, hi)} "
                                       f"for {mins:.0f} min")

        for sensor, since in self.fail_since.items():
            if now - since >= ALERT_SENSOR_FAIL_S:
                mins = (now - since) / 60
                c[f"fail:{sensor}"] = f"{LABEL.get(sensor, sensor)} sensor failing for {mins:.0f} min"

        return c

    def tick(self, now: float) -> List[Tuple[str, str, str]]:
        """Advance. Returns (kind, key, text) for every notification due.

        kind is "raised", "repeat" or "cleared".
        """
        out: List[Tuple[str, str, str]] = []
        current = self.conditions(now)

        for key, text in current.items():
            a = self.active.get(key)
            if a is None:
                self.active[key] = {"text": text, "since": now, "sent": now}
                out.append(("raised", key, text))
            else:
                a["text"] = text
                if now - a["sent"] >= ALERT_REPEAT_S:
                    a["sent"] = now
                    out.append(("repeat", key, text))

        for key in list(self.active):
            if key not in current:
                a = self.active.pop(key)
                if key in EVENTS:
                    continue
                mins = max(1, round((now - a["since"]) / 60))
                out.append(("cleared", key, f"cleared after {mins} min: {a['text']}"))

        return out

    def summary(self, now: float) -> dict:
        """What the dashboard shows. Published retained on hydro/state/alerts."""
        return {"active": [{"key": k, "text": a["text"], "since": int(a["since"])}
                           for k, a in sorted(self.active.items())],
                "count": len(self.active),
                "timestamp": int(now)}


# ---------------------------------------------------------------- delivery

class Notifier:
    """ntfy over plain http. Never raises: a notification that cannot be sent is
    printed, and the condition stays active so the reminder tries again."""

    def __init__(self, topic: str = ALERT_NTFY_TOPIC, server: str = ALERT_NTFY_SERVER,
                 dry_run: bool = False) -> None:
        self.topic = topic.strip()
        self.server = server.rstrip("/")
        self.dry_run = dry_run

    @property
    def enabled(self) -> bool:
        return bool(self.topic) and not self.dry_run

    def send(self, kind: str, key: str, text: str) -> bool:
        title = {"raised": "hydro", "repeat": "hydro, still", "cleared": "hydro, cleared"}[kind]
        urgent = kind != "cleared" and key in URGENT
        line = f"  [{time.strftime('%H:%M:%S')}] {kind:<7} {key:<18} {text}"
        print(line, flush=True)
        if not self.enabled:
            return False
        req = urllib.request.Request(
            f"{self.server}/{self.topic}",
            data=text.encode("utf-8"),
            headers={"Title": title,
                     "Priority": "high" if urgent else "default",
                     "Tags": "warning" if urgent else ("white_check_mark" if kind == "cleared" else "droplet")},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  ntfy failed: {exc}", flush=True)
            return False


# ---------------------------------------------------------------- the service

def main() -> int:
    ap = argparse.ArgumentParser(description="hydro alerts")
    ap.add_argument("--broker", default=MQTT_HOST)
    ap.add_argument("--port", type=int, default=MQTT_PORT)
    ap.add_argument("--dry-run", action="store_true", help="print, send nothing")
    ap.add_argument("--test", action="store_true", help="send one notification and exit")
    args = ap.parse_args()

    notifier = Notifier(dry_run=args.dry_run)
    if args.test:
        ok = notifier.send("raised", "test", "test notification from the tower")
        print("sent" if ok else ("printed only, no ALERT_NTFY_TOPIC set" if not notifier.topic
                                 else "send failed"))
        return 0 if ok or not notifier.topic else 1

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("paho-mqtt is not installed. pip install -r alerts/requirements.txt",
              file=sys.stderr)
        return 1

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    rules = Rules()
    q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)

    def on_connect(client, _u, _f, reason_code, *_a):
        ok = getattr(reason_code, "is_failure", None)
        good = (not ok()) if callable(ok) else (reason_code == 0)
        if not good:
            print(f"  connect refused: {reason_code}")
            return
        print(f"  connected to {args.broker}:{args.port}")
        client.subscribe([(SENSOR_PREFIX + "#", 1), (STATE_PREFIX + "#", 1)])

    def on_message(_client, _u, msg):
        try:
            body = json.loads(msg.payload.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(body, dict):
            return
        if msg.topic.startswith(SENSOR_PREFIX):
            item = ("r", msg.topic[len(SENSOR_PREFIX):], body)
        elif msg.topic.startswith(STATE_PREFIX) and msg.topic != TOPIC_ALERTS:
            item = ("s", msg.topic[len(STATE_PREFIX):], body)
        else:
            return
        try:
            q.put_nowait(item)
        except queue.Full:
            pass

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hydro-alerts")
    except AttributeError:
        client = mqtt.Client(client_id="hydro-alerts")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect_async(args.broker, args.port, 60)
        client.loop_start()
    except Exception as exc:                               # noqa: BLE001
        print(f"  {exc}", file=sys.stderr)
        return 1

    where = "dry run" if args.dry_run else (f"ntfy topic {notifier.topic}" if notifier.topic
                                            else "no ntfy topic, printing only")
    print(f"alerts: {where}")

    last_publish = 0.0
    try:
        while _running:
            time.sleep(0.5)
            while True:
                try:
                    kind, name, body = q.get_nowait()
                except queue.Empty:
                    break
                ts = body.get("timestamp")
                ts = float(ts) if isinstance(ts, (int, float)) else time.time()
                if kind == "r":
                    value = body.get("value")
                    rules.feed_reading(name, float(value) if isinstance(value, (int, float)) else None,
                                       bool(body.get("valid")), ts)
                else:
                    rules.feed_state(name, body, ts)

            now = time.time()
            changed = rules.tick(now)
            for kind, key, text in changed:
                notifier.send(kind, key, text)
            # retained, so the dashboard can show what is active. refreshed on any
            # change and once a minute regardless, as a heartbeat of our own
            if changed or now - last_publish >= 60:
                client.publish(TOPIC_ALERTS, json.dumps(rules.summary(now)), qos=1, retain=True)
                last_publish = now
    finally:
        client.loop_stop()
        client.disconnect()
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
