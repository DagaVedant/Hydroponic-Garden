"""The dashboard.

    python -m web.app                    http://0.0.0.0:8080
    python -m web.app --port 5000
    python -m web.app --demo             seed a fake day and open on that

Two pages. `/` is the overview: the tower drawn with values where they are
measured, and trend cards under it. `/timeline` is what the system has been doing.

**Reads sqlite, writes mqtt.** It never talks to a sensor and never touches a gpio.
Display comes out of the database the ingest service fills; a button publishes on
`hydro/cmd/...` and the control service picks it up on its next sweep. That means
the dashboard can crash, be restarted, or be open in six tabs without any of it
reaching the hardware.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from typing import List, Optional

sys.path.insert(0, ".")

from flask import Flask, jsonify, render_template, request      # noqa: E402

from control.config import BANDS, MAX_FILL_DEPTH_MM, MQTT_HOST      # noqa: E402
from control.hardware import MQTT_PORT                                 # noqa: E402
from control.sensors.level import TankLevel                            # noqa: E402
from db.store import DB_PATH, Store                                    # noqa: E402

app = Flask(__name__)
app.config["db_path"] = DB_PATH
# templates are cached unless debug is on, which makes editing one look like
# it did nothing. cheap to reload on a single-user lan dashboard
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
# and stop the browser caching last minute css
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# what each sensor is called. the band it should sit in is BANDS in
# control/config.py, the same one the alerts fire on, so the two never disagree
SENSORS = [
    ("water/level",  "water level", "L",     *BANDS["water/level"]),
    ("water/ph",     "pH",          "pH",    *BANDS["water/ph"]),
    ("water/ec",     "EC",          "uS/cm", *BANDS["water/ec"]),
    ("water/temp",   "water temp",  "C",     *BANDS["water/temp"]),
    ("air/temp",     "air temp",    "C",     *BANDS["air/temp"]),
    ("air/humidity", "humidity",    "%RH",   *BANDS["air/humidity"]),
]
LABELS = {k: (label, unit, lo, hi) for k, label, unit, lo, hi in SENSORS}
TANK_FULL_L = round(TankLevel.depth_to_litres(MAX_FILL_DEPTH_MM), 1)   # the fill line


def store() -> Store:
    return Store(app.config["db_path"])


# ---------------------------------------------------------------- mqtt commands

class Commands:
    """Publishes to hydro/cmd/... Degrades to a no-op if there is no broker."""

    def __init__(self, host: str = MQTT_HOST, port: int = MQTT_PORT) -> None:
        self.host, self.port, self.client = host, port, None
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            return
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                      client_id="hydro-web")
        except AttributeError:
            self.client = mqtt.Client(client_id="hydro-web")
        try:
            self.client.connect_async(host, port, 60)
            self.client.loop_start()
        except Exception:                                        # noqa: BLE001
            self.client = None

    def send(self, name: str, body: dict) -> bool:
        if self.client is None:
            return False
        try:
            info = self.client.publish(f"hydro/cmd/{name}",
                                       json.dumps(body), qos=1)
            return info.rc == 0
        except Exception:                                        # noqa: BLE001
            return False


commands = Commands()


# ---------------------------------------------------------------- helpers

def state_of(s: Store, topic: str) -> dict:
    row = s.latest_event(f"hydro/state/{topic}")
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
    """Everything the overview page needs, in one query pass."""
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
            delta = round(pts[-1] - pts[0], 2) if len(pts) > 1 else None
            out.append({
                "sensor": key, "label": label, "unit": unit,
                "value": value, "valid": valid,
                "age": now - r["ts"] if r else None,
                "note": r["note"] if r else "",
                "lo": lo, "hi": hi,
                "status": band_status(key, value, valid),
                "series": pts, "delta": delta,
            })

        online = state_of(s, "online")
        heartbeat = online.get("timestamp")
        alerts = state_of(s, "alerts")
        return {
            "sensors": out,
            "lights": state_of(s, "lights"),
            "dosing": state_of(s, "dosing"),
            # what the alerts service is currently shouting about, if it runs
            "alerts": [a for a in alerts.get("active", []) if isinstance(a, dict)],
            "tank_full_l": TANK_FULL_L,
            "online": bool(online.get("online")) and (
                heartbeat is not None and now - heartbeat < 300),
            "last_seen": heartbeat,
            "now": now,
        }
    finally:
        s.close()


def timeline(limit: int = 80) -> List[dict]:
    """What happened, newest first.

    Derived rather than stored. The state topics are republished every sweep, so
    the raw event table is mostly the same row over and over; only the transitions
    are worth showing.
    """
    s = store()
    try:
        events: List[dict] = []

        last_dose_key = None
        last_lit = None
        pending_dose = None
        for row in reversed(s.events_since(int(time.time()) - 7 * 86400, limit=4000)):
            try:
                body = json.loads(row["payload"])
            except (json.JSONDecodeError, ValueError):
                continue

            if row["topic"] == "hydro/state/dosing":
                if body.get("ml") and body.get("channel"):
                    key = (body["channel"], body["ml"], row["ts"] // 60)
                    if key != last_dose_key:
                        last_dose_key = key
                        channel = body["channel"]
                        pending_dose = {
                            "ts": row["ts"], "kind": "dose",
                            # carried as fields, not scraped back out of `text`
                            # later. the summary needs these, and a display
                            # string is not a data format
                            "channel": channel, "ml": body["ml"],
                            "text": f"{body['ml']} mL "
                                    f"{CHANNEL_LABEL.get(channel, channel)}"}
                        events.append(pending_dose)
                elif body.get("state") == "fault" and body.get("fault"):
                    events.append({"ts": row["ts"], "kind": "fault",
                                   "text": f"dosing: {body['fault']}"})
                elif (pending_dose is not None
                      and str(body.get("note", "")).startswith("verified")):
                    # attach the outcome to the dose rather than listing it twice.
                    # "5.0 mL micro, verified" is one event, not two
                    pending_dose["text"] += ", " + body["note"]
                    pending_dose = None

            elif row["topic"] == "hydro/state/lights":
                lit = bool(body.get("on"))
                if last_lit is not None and lit != last_lit:
                    events.append({
                        "ts": row["ts"], "kind": "light",
                        "text": f"lights {'on' if lit else 'off'}"
                                f"{', ' + str(round(body.get('duty', 0) * 100)) + '%' if lit else ''}"})
                last_lit = lit

        # a sensor that fails usually fails for several sweeps in a row. one entry
        # per sweep would bury everything else, so consecutive identical faults
        # collapse into one line carrying how long it went on
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
            label = LABELS.get(sensor, (sensor,))[0]
            span = ""
            if run["n"] > 1:
                mins = max(1, round((run["last"] - run["ts"]) / 60))
                span = f" ({run['n']} readings over {mins} min)"
            events.append({"ts": run["ts"], "kind": "fault",
                           "text": f"{label}: {note}{span}"})

        events.sort(key=lambda e: e["ts"], reverse=True)
        return events[:limit]
    finally:
        s.close()


BOTTLE_ML = 1000.0          # 1 L concentrate bottles
CHANNEL_LABEL = {"micro": "FloraMicro", "gro": "FloraGro", "ph_down": "pH Down"}


def activity_summary(events: List[dict]) -> dict:
    """Totals worth knowing before reading the log itself.

    The concentrate estimate is arithmetic, not a measurement. There is no float
    switch in the bottles by design: dose verification catches an empty bottle
    along with a slipped tube and a dead pump, which a float switch cannot. But
    that means the only way to know how much is left is to add up what was dosed,
    so it is labelled as an estimate and reset by hand on a refill.
    """
    now = int(time.time())
    day = now - 86400

    consumed = {"micro": 0.0, "gro": 0.0, "ph_down": 0.0}
    doses_24h = faults_24h = 0
    last_fault = None

    for e in events:
        if e["kind"] == "dose":
            channel = e.get("channel")
            if channel in consumed:
                consumed[channel] += float(e.get("ml") or 0.0)
            if e["ts"] >= day:
                doses_24h += 1
        elif e["kind"] == "fault":
            if e["ts"] >= day:
                faults_24h += 1
            if last_fault is None:
                last_fault = e

    bottles = [{
        "channel": c,
        "label": CHANNEL_LABEL.get(c, c),
        "used_ml": round(consumed.get(c, 0.0), 1),
        "remaining_pct": max(0, round(100 * (1 - consumed.get(c, 0.0) / BOTTLE_ML))),
    } for c in ("micro", "gro", "ph_down")]

    return {
        "doses_24h": doses_24h,
        "faults_24h": faults_24h,
        "last_fault": last_fault,
        "bottles": bottles,
        "window_start": day,
        "now": now,
    }


# ---------------------------------------------------------------- demo data

DEMO_LIGHT_ON, DEMO_LIGHT_OFF = 6.0, 22.0

def seed_demo(path: str) -> int:
    """Fill a throwaway database with a plausible day and return the row count.

    The interface is unreadable against an empty table, and until there is a tower
    to read an empty table is all there is. This is for looking at the dashboard,
    not a test fixture and not a stand-in for the pi: it writes whatever file it is
    handed, so hand it a temporary one.
    """
    s = Store(path)
    s.migrate(verbose=False)

    now = int(time.time())
    start, step = now - 86400, 300               # a day, one sweep every 5 minutes

    # a slow random walk per sensor, seeded near the middle of each target band
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

    # one stretch where the ph probe drops out, so the fault log is not empty
    dropout = set(range(198, 209))

    rows = []
    for i, ts in enumerate(range(start, now, step)):
        lit = lit_at(ts)
        for sensor in walk:
            walk[sensor] += drift[sensor] + random.uniform(-jitter[sensor],
                                                           jitter[sensor])
        values = dict(walk)
        # the room warms and dries while 54 W of led is on
        values["air/temp"] += 1.9 if lit else 0.0
        values["air/humidity"] -= 6.5 if lit else 0.0
        values["water/temp"] += 0.7 if lit else 0.0

        for sensor, value in values.items():
            if sensor == "water/ph" and i in dropout:
                rows.append((ts, sensor, None, units[sensor], False,
                             "ads1115 read failed"))
            else:
                rows.append((ts, sensor, round(value, 2), units[sensor], True, ""))
    s.add_readings(rows)

    def event(ts: int, topic: str, body: dict) -> None:
        body.setdefault("timestamp", ts)
        s.add_event(ts, f"hydro/state/{topic}", json.dumps(body))

    # the control loop republishes state every sweep. half-hourly is enough to
    # carry the two transitions the timeline actually draws
    for ts in range(start, now, 1800):
        lit = lit_at(ts)
        event(ts, "lights", {"on": lit, "duty": 1.0 if lit else 0.0,
                             "mode": "schedule", "on_hour": DEMO_LIGHT_ON,
                             "off_hour": DEMO_LIGHT_OFF})

    # doses, each followed by the verification that closes it out
    for hours_ago, channel, ml, sensor, moved in (
            (21.5, "micro", 4.6, "water/ec", 101.2),
            (17.0, "gro", 3.8, "water/ec", 68.4),
            (11.5, "micro", 5.0, "water/ec", 110.0),
            (6.0, "ph_down", 0.8, "water/ph", -0.048),
            (2.5, "gro", 2.4, "water/ec", 43.2)):
        ts = now - int(hours_ago * 3600)
        event(ts, "dosing", {"state": "mixing", "channel": channel, "ml": ml,
                             "enabled": True, "calibrated": False})
        event(ts + 300, "dosing", {"state": "idle",
                                   "note": f"verified, moved {moved:+.3g}"})

    event(now, "online", {"online": True})
    event(now, "lights", {"on": lit_at(now), "duty": 1.0 if lit_at(now) else 0.0,
                          "mode": "schedule", "on_hour": DEMO_LIGHT_ON,
                          "off_hour": DEMO_LIGHT_OFF})
    event(now, "dosing", {"state": "idle", "enabled": True, "calibrated": False,
                          "runtime_last_hour_s": 0.0, "cap_s": 30.0, "fault": "",
                          "note": "in band"})
    event(now, "alerts", {"active": [], "count": 0})

    count = s.count()
    s.close()
    return count


# ---------------------------------------------------------------- routes

@app.route("/")
def overview():
    return render_template("overview.html", page="overview")


@app.route("/timeline")
def timeline_page():
    return render_template("timeline.html", page="timeline")


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
    ok = commands.send(name, body)
    # the button reports whether the message left, not whether it worked. the real
    # answer arrives on the next sweep when the state topic changes
    return jsonify({"ok": ok, "sent": body,
                    "error": "" if ok else "no broker"}), (200 if ok else 503)


def main() -> int:
    ap = argparse.ArgumentParser(description="hydroponic dashboard")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--demo", action="store_true",
                    help="seed a throwaway database with a fake day and open on it")
    args = ap.parse_args()

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

    print(f"dashboard on http://{args.host}:{args.port}  ({rows} readings in {args.db})")
    if commands.client is None:
        print("  no broker, buttons will report 'no broker' rather than pretending")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
