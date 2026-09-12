from __future__ import annotations

import argparse
import collections
import json
import os
import queue
import random
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Deque, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request, send_file

from config import (ALERT_HEARTBEAT_S, ALERT_LEVEL_LOW_L, ALERT_LEVEL_RISE_L,
                    ALERT_LEVEL_RISE_WINDOW_S, ALERT_NTFY_SERVER, ALERT_NTFY_TOPIC,
                    ALERT_OUT_OF_BAND_S, ALERT_REPEAT_S, ALERT_SENSOR_FAIL_S, BANDS,
                    MAX_FILL_DEPTH_MM, MQTT_HOST)
from control import MQTT_PORT, MQTT_TOPIC_PREFIX, TankLevel
from store import DB_PATH, Store

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "dashboard.html")
TOWER = os.path.join(HERE, "..", "images", "tower-assembly.png")

app = Flask(__name__)
app.config["db_path"] = DB_PATH

SENSORS = [
    ("water/level",  "water level", "L",     *BANDS["water/level"]),
    ("water/ph",     "pH",          "pH",    *BANDS["water/ph"]),
    ("water/ec",     "EC",          "uS/cm", *BANDS["water/ec"]),
    ("water/temp",   "water temp",  "C",     *BANDS["water/temp"]),
    ("air/temp",     "air temp",    "C",     *BANDS["air/temp"]),
    ("air/humidity", "humidity",    "%RH",   *BANDS["air/humidity"]),
]
LABELS = {k: (label, unit, lo, hi) for k, label, unit, lo, hi in SENSORS}
TANK_FULL_L = round(TankLevel.depth_to_litres(MAX_FILL_DEPTH_MM), 1)
BOTTLE_ML = 1000.0
CHANNEL_LABEL = {"micro": "FloraMicro", "gro": "FloraGro", "ph_down": "pH Down"}

SENSOR_PREFIX = f"{MQTT_TOPIC_PREFIX}/sensor/"
STATE_PREFIX = f"{MQTT_TOPIC_PREFIX}/state/"
TOPIC_ALERTS = f"{MQTT_TOPIC_PREFIX}/state/alerts"


def store() -> Store:
    return Store(app.config["db_path"])


def _mqtt_client(client_id: str):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return None
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


class Commands:
    def __init__(self, host: str = MQTT_HOST, port: int = MQTT_PORT) -> None:
        self.client = _mqtt_client("hydro-web")
        if self.client is None:
            return
        try:
            self.client.connect_async(host, port, 60)
            self.client.loop_start()
        except Exception:
            self.client = None

    def send(self, name: str, body: dict) -> bool:
        if self.client is None:
            return False
        try:
            return self.client.publish(f"{MQTT_TOPIC_PREFIX}/cmd/{name}", json.dumps(body), qos=1).rc == 0
        except Exception:
            return False


commands: Optional[Commands] = None


def state_of(s: Store, topic: str) -> dict:
    row = s.latest_event(f"{STATE_PREFIX}{topic}")
    if not row:
        return {}
    try:
        return json.loads(row["payload"])
    except (json.JSONDecodeError, ValueError):
        return {}


def band_status(sensor: str, value: Optional[float], valid: bool) -> str:
    if not valid or value is None:
        return "bad"
    _, _, lo, hi = LABELS.get(sensor, ("", "", None, None))
    if lo is None:
        return "ok"
    return "ok" if lo <= value <= hi else "warn"


def snapshot() -> dict:
    s = store()
    try:
        latest = {r["sensor"]: r for r in s.latest()}
        now = int(time.time())
        since = now - 24 * 3600
        out = []
        for key, label, unit, lo, hi in SENSORS:
            r = latest.get(key)
            value = r["value"] if r else None
            valid = bool(r["valid"]) if r else False
            pts = [round(p["v"], 3) for p in s.series(key, since)] if r else []
            out.append({"sensor": key, "label": label, "unit": unit, "value": value,
                        "valid": valid, "age": now - r["ts"] if r else None,
                        "note": r["note"] if r else "", "lo": lo, "hi": hi,
                        "status": band_status(key, value, valid), "series": pts,
                        "delta": round(pts[-1] - pts[0], 2) if len(pts) > 1 else None})
        online = state_of(s, "online")
        heartbeat = online.get("timestamp")
        alerts = state_of(s, "alerts")
        return {
            "sensors": out,
            "lights": state_of(s, "lights"),
            "dosing": state_of(s, "dosing"),
            "alerts": [a for a in alerts.get("active", []) if isinstance(a, dict)],
            "tank_full_l": TANK_FULL_L,
            "online": bool(online.get("online")) and (heartbeat is not None and now - heartbeat < 300),
            "last_seen": heartbeat,
            "now": now,
        }
    finally:
        s.close()


def timeline(limit: int = 80) -> List[dict]:
    s = store()
    try:
        events: List[dict] = []
        last_dose_key = last_lit = pending_dose = None
        for row in reversed(s.events_since(int(time.time()) - 7 * 86400, limit=4000)):
            try:
                body = json.loads(row["payload"])
            except (json.JSONDecodeError, ValueError):
                continue
            if row["topic"] == f"{STATE_PREFIX}dosing":
                if body.get("ml") and body.get("channel"):
                    key = (body["channel"], body["ml"], row["ts"] // 60)
                    if key != last_dose_key:
                        last_dose_key = key
                        channel = body["channel"]
                        pending_dose = {"ts": row["ts"], "kind": "dose", "channel": channel,
                                        "ml": body["ml"],
                                        "text": f"{body['ml']} mL {CHANNEL_LABEL.get(channel, channel)}"}
                        events.append(pending_dose)
                elif body.get("state") == "fault" and body.get("fault"):
                    events.append({"ts": row["ts"], "kind": "fault", "text": f"dosing: {body['fault']}"})
                elif pending_dose is not None and str(body.get("note", "")).startswith("verified"):
                    pending_dose["text"] += ", " + body["note"]
                    pending_dose = None
            elif row["topic"] == f"{STATE_PREFIX}lights":
                lit = bool(body.get("on"))
                if last_lit is not None and lit != last_lit:
                    pct = f", {round(body.get('duty', 0) * 100)}%" if lit else ""
                    events.append({"ts": row["ts"], "kind": "light",
                                   "text": f"lights {'on' if lit else 'off'}{pct}"})
                last_lit = lit

        runs: List[dict] = []
        for row in sorted(s.faults(limit=400), key=lambda r: r["ts"]):
            key = (row["sensor"], row["note"])
            if runs and runs[-1]["key"] == key and row["ts"] - runs[-1]["last"] <= 300:
                runs[-1]["last"] = row["ts"]
                runs[-1]["n"] += 1
            else:
                runs.append({"key": key, "ts": row["ts"], "last": row["ts"], "n": 1})
        for run in runs:
            sensor, note = run["key"]
            span = ""
            if run["n"] > 1:
                span = f" ({run['n']} readings over {max(1, round((run['last'] - run['ts']) / 60))} min)"
            events.append({"ts": run["ts"], "kind": "fault",
                           "text": f"{LABELS.get(sensor, (sensor,))[0]}: {note}{span}"})

        events.sort(key=lambda e: e["ts"], reverse=True)
        return events[:limit]
    finally:
        s.close()


def activity_summary(events: List[dict]) -> dict:
    now = int(time.time())
    day = now - 86400
    consumed = {"micro": 0.0, "gro": 0.0, "ph_down": 0.0}
    doses_24h = faults_24h = 0
    last_fault = None
    for e in events:
        if e["kind"] == "dose":
            if e.get("channel") in consumed:
                consumed[e["channel"]] += float(e.get("ml") or 0.0)
            if e["ts"] >= day:
                doses_24h += 1
        elif e["kind"] == "fault":
            if e["ts"] >= day:
                faults_24h += 1
            if last_fault is None:
                last_fault = e
    bottles = [{"channel": c, "label": CHANNEL_LABEL.get(c, c),
                "used_ml": round(consumed[c], 1),
                "remaining_pct": max(0, round(100 * (1 - consumed[c] / BOTTLE_ML)))}
               for c in ("micro", "gro", "ph_down")]
    return {"doses_24h": doses_24h, "faults_24h": faults_24h, "last_fault": last_fault,
            "bottles": bottles, "window_start": day, "now": now}


@app.route("/")
def page():
    return send_file(PAGE, max_age=0)


@app.route("/tower.png")
def tower():
    return send_file(TOWER, max_age=3600)


@app.route("/api/snapshot")
def api_snapshot():
    return jsonify(snapshot())


@app.route("/api/timeline")
def api_timeline():
    events = timeline()
    return jsonify({"events": events, "summary": activity_summary(events)})


@app.route("/api/cmd/<name>", methods=["POST"])
def api_cmd(name: str):
    if name not in ("lights", "dose"):
        return jsonify({"ok": False, "error": "unknown command"}), 404
    body = request.get_json(silent=True) or {}
    ok = commands.send(name, body) if commands is not None else False
    return jsonify({"ok": ok, "sent": body, "error": "" if ok else "no broker"}), (200 if ok else 503)


DEMO_LIGHT_ON, DEMO_LIGHT_OFF = 6.0, 22.0


def seed_demo(path: str) -> int:
    s = Store(path)
    s.migrate(verbose=False)
    now = int(time.time())
    start, step = now - 86400, 300

    walk = {"water/level": TANK_FULL_L - 0.3, "water/ph": 5.72, "water/ec": 1255.0,
            "water/temp": 20.6, "air/temp": 21.6, "air/humidity": 58.0}
    drift = {"water/level": -0.006, "water/ph": 0.0011, "water/ec": -0.42,
             "water/temp": 0.0, "air/temp": 0.0, "air/humidity": 0.0}
    jitter = {"water/level": 0.012, "water/ph": 0.008, "water/ec": 2.5,
              "water/temp": 0.05, "air/temp": 0.10, "air/humidity": 0.45}
    units = {"water/level": "L", "water/ph": "pH", "water/ec": "uS/cm",
             "water/temp": "C", "air/temp": "C", "air/humidity": "%RH"}

    def lit_at(ts: int) -> bool:
        t = time.localtime(ts)
        return DEMO_LIGHT_ON <= t.tm_hour + t.tm_min / 60.0 < DEMO_LIGHT_OFF

    dropout = set(range(198, 209))
    rows = []
    for i, ts in enumerate(range(start, now + 1, step)):
        lit = lit_at(ts)
        for sensor in walk:
            walk[sensor] += drift[sensor] + random.uniform(-jitter[sensor], jitter[sensor])
        values = dict(walk)
        values["air/temp"] += 1.9 if lit else 0.0
        values["air/humidity"] -= 6.5 if lit else 0.0
        values["water/temp"] += 0.7 if lit else 0.0
        for sensor, value in values.items():
            if sensor == "water/ph" and i in dropout:
                rows.append((ts, sensor, None, units[sensor], False, "ads1115 read failed"))
            else:
                rows.append((ts, sensor, round(value, 2), units[sensor], True, ""))
    s.add_readings(rows)

    def event(ts: int, topic: str, body: dict) -> None:
        body.setdefault("timestamp", ts)
        s.add_event(ts, f"{STATE_PREFIX}{topic}", json.dumps(body))

    for ts in range(start, now, 1800):
        lit = lit_at(ts)
        event(ts, "lights", {"on": lit, "duty": 1.0 if lit else 0.0, "mode": "schedule",
                             "on_hour": DEMO_LIGHT_ON, "off_hour": DEMO_LIGHT_OFF})
    for hours_ago, channel, ml, moved in ((21.5, "micro", 4.6, 101.2), (17.0, "gro", 3.8, 68.4),
                                          (11.5, "micro", 5.0, 110.0), (6.0, "ph_down", 0.8, -0.048),
                                          (2.5, "gro", 2.4, 43.2)):
        ts = now - int(hours_ago * 3600)
        event(ts, "dosing", {"state": "mixing", "channel": channel, "ml": ml,
                             "enabled": True, "calibrated": False})
        event(ts + 300, "dosing", {"state": "idle", "note": f"verified, moved {moved:+.3g}"})
    event(now, "online", {"online": True})
    event(now, "lights", {"on": lit_at(now), "duty": 1.0 if lit_at(now) else 0.0, "mode": "schedule",
                          "on_hour": DEMO_LIGHT_ON, "off_hour": DEMO_LIGHT_OFF})
    event(now, "dosing", {"state": "idle", "enabled": True, "calibrated": False,
                          "runtime_last_hour_s": 0.0, "cap_s": 30.0, "fault": "", "note": "in band"})
    event(now, "alerts", {"active": [], "count": 0})
    count = s.count()
    s.close()
    return count


LABEL = {k: v[0] for k, v in LABELS.items()}
UNIT = {"water/level": " L", "water/ph": "", "water/ec": " uS/cm",
        "water/temp": " C", "air/temp": " C", "air/humidity": "%"}
URGENT = ("level_rise", "dosing_fault", "offline", "level_low")
EVENTS = ("level_rise",)


def _fmt(sensor: str, value: float) -> str:
    if sensor == "water/ec":
        return f"{value:.0f}{UNIT[sensor]}"
    return f"{value:.2f}{UNIT[sensor]}".replace(".00", "")


class Rules:
    def __init__(self) -> None:
        self.last: Dict[str, Tuple[float, float]] = {}
        self.out_since: Dict[str, float] = {}
        self.fail_since: Dict[str, float] = {}
        self.level_hist: Deque[Tuple[float, float]] = collections.deque()
        self.dosing_fault = ""
        self.online: Optional[bool] = None
        self.heartbeat_ts: Optional[float] = None
        self.active: Dict[str, dict] = {}

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

    def conditions(self, now: float) -> Dict[str, str]:
        c: Dict[str, str] = {}
        if self.online is False:
            c["offline"] = "control loop is offline. the pi is down or the service crashed"
        elif self.heartbeat_ts is not None and now - self.heartbeat_ts > ALERT_HEARTBEAT_S:
            c["offline"] = f"no heartbeat from the control loop for {(now - self.heartbeat_ts) / 60:.0f} min"
        if self.dosing_fault:
            c["dosing_fault"] = (f"dosing stopped: {self.dosing_fault}. clear the fault on the "
                                 f"dashboard once fixed")
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
                c[f"band:{sensor}"] = (f"{LABEL.get(sensor, sensor)} is "
                                       f"{_fmt(sensor, self.last[sensor][1])}, outside "
                                       f"{_fmt(sensor, lo)} to {_fmt(sensor, hi)} for "
                                       f"{(now - since) / 60:.0f} min")
        for sensor, since in self.fail_since.items():
            if now - since >= ALERT_SENSOR_FAIL_S:
                c[f"fail:{sensor}"] = (f"{LABEL.get(sensor, sensor)} sensor failing for "
                                       f"{(now - since) / 60:.0f} min")
        return c

    def tick(self, now: float) -> List[Tuple[str, str, str]]:
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
        return {"active": [{"key": k, "text": a["text"], "since": int(a["since"])}
                           for k, a in sorted(self.active.items())],
                "count": len(self.active), "timestamp": int(now)}


class Notifier:
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
        print(f"  [{time.strftime('%H:%M:%S')}] alert {kind:<7} {key:<18} {text}", flush=True)
        if not self.enabled:
            return False
        req = urllib.request.Request(
            f"{self.server}/{self.topic}", data=text.encode("utf-8"), method="POST",
            headers={"Title": title, "Priority": "high" if urgent else "default",
                     "Tags": "warning" if urgent else ("white_check_mark" if kind == "cleared" else "droplet")})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  ntfy failed: {exc}", flush=True)
            return False


class Watcher(threading.Thread):
    def __init__(self, notifier: Notifier, host: str = MQTT_HOST, port: int = MQTT_PORT) -> None:
        super().__init__(name="alerts", daemon=True)
        self.notifier = notifier
        self.host, self.port = host, port
        self.rules = Rules()
        self.q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)
        self.client = _mqtt_client("hydro-alerts")
        self.stop = threading.Event()

    def _on_connect(self, client, _u, _f, reason_code, *_a):
        ok = getattr(reason_code, "is_failure", None)
        if not ((not ok()) if callable(ok) else (reason_code == 0)):
            print(f"  alerts: connect refused: {reason_code}")
            return
        print(f"  alerts: connected to {self.host}:{self.port}")
        client.subscribe([(SENSOR_PREFIX + "#", 1), (STATE_PREFIX + "#", 1)])

    def _on_message(self, _client, _u, msg):
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
            self.q.put_nowait(item)
        except queue.Full:
            pass

    def run(self) -> None:
        if self.client is None:
            print("  alerts: paho-mqtt not installed, not watching")
            return
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        try:
            self.client.connect_async(self.host, self.port, 60)
            self.client.loop_start()
        except Exception as exc:
            print(f"  alerts: {exc}")
            return
        last_publish = 0.0
        try:
            while not self.stop.wait(0.5):
                while True:
                    try:
                        kind, name, body = self.q.get_nowait()
                    except queue.Empty:
                        break
                    ts = body.get("timestamp")
                    ts = float(ts) if isinstance(ts, (int, float)) else time.time()
                    if kind == "r":
                        value = body.get("value")
                        self.rules.feed_reading(name, float(value) if isinstance(value, (int, float)) else None,
                                                bool(body.get("valid")), ts)
                    else:
                        self.rules.feed_state(name, body, ts)
                now = time.time()
                changed = self.rules.tick(now)
                for kind, key, text in changed:
                    self.notifier.send(kind, key, text)
                if changed or now - last_publish >= 60:
                    self.client.publish(TOPIC_ALERTS, json.dumps(self.rules.summary(now)), qos=1, retain=True)
                    last_publish = now
        finally:
            self.client.loop_stop()
            self.client.disconnect()


def main() -> int:
    global commands
    ap = argparse.ArgumentParser(description="hydroponic dashboard and alerts")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--broker", default=MQTT_HOST)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--demo", action="store_true", help="seed a throwaway database with a fake day")
    ap.add_argument("--no-alerts", action="store_true", help="dashboard only")
    ap.add_argument("--alerts-only", action="store_true", help="no dashboard, just the watcher")
    ap.add_argument("--dry-run", action="store_true", help="alerts print, send nothing")
    ap.add_argument("--test-alert", action="store_true", help="send one notification and exit")
    args = ap.parse_args()

    notifier = Notifier(dry_run=args.dry_run)
    if args.test_alert:
        ok = notifier.send("raised", "test", "test notification from the tower")
        print("sent" if ok else ("printed only, no ALERT_NTFY_TOPIC set" if not notifier.topic
                                 else "send failed"))
        return 0 if ok or not notifier.topic else 1

    watcher = None
    if not args.no_alerts:
        watcher = Watcher(notifier, host=args.broker)
        where = "dry run" if args.dry_run else (f"ntfy topic {notifier.topic}" if notifier.topic
                                                else "no ntfy topic, printing only")
        print(f"alerts: {where}")
        watcher.start()
        if args.alerts_only:
            try:
                while watcher.is_alive():
                    watcher.join(1.0)
            except KeyboardInterrupt:
                watcher.stop.set()
                watcher.join(3.0)
            return 0

    if args.demo:
        args.db = os.path.join(tempfile.gettempdir(), "hydro-demo.sqlite")
        if os.path.exists(args.db):
            os.remove(args.db)
        print(f"demo: seeded {seed_demo(args.db)} readings into {args.db}")

    app.config["db_path"] = args.db
    s = Store(args.db)
    s.migrate(verbose=False)
    rows = s.count()
    s.close()
    commands = Commands(host=args.broker)

    print(f"dashboard on http://{args.host}:{args.port}  ({rows} readings in {args.db})")
    if commands.client is None:
        print("  no broker, buttons will report 'no broker' rather than pretending")
    try:
        app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
    finally:
        if watcher is not None:
            watcher.stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
