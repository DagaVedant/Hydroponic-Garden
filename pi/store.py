from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import math
import os
import queue
import signal
import sqlite3
import sys
import time
from typing import Iterable, List, Optional, Sequence, Tuple

from config import MQTT_HOST

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hydro.sqlite")
MQTT_PORT = 1883
SENSOR_PREFIX = "hydro/sensor/"
STATE_PREFIX = "hydro/state/"

SCHEMA = [
    ("001_initial", """
CREATE TABLE IF NOT EXISTS reading (
    ts      INTEGER NOT NULL,
    sensor  TEXT    NOT NULL,
    value   REAL,
    unit    TEXT    NOT NULL,
    valid   INTEGER NOT NULL,
    note    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (sensor, ts)
);
CREATE INDEX IF NOT EXISTS reading_sensor_ts ON reading (sensor, ts);
CREATE INDEX IF NOT EXISTS reading_ts        ON reading (ts);

CREATE INDEX IF NOT EXISTS reading_invalid   ON reading (ts) WHERE valid = 0;

CREATE TABLE IF NOT EXISTS event (
    ts      INTEGER NOT NULL,
    topic   TEXT    NOT NULL,
    payload TEXT    NOT NULL,
    PRIMARY KEY (topic, ts)
);
CREATE INDEX IF NOT EXISTS event_ts ON event (ts);
"""),
]

INSERT_READING = """
INSERT INTO reading (ts, sensor, value, unit, valid, note) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (sensor, ts) DO NOTHING
"""
INSERT_EVENT = """
INSERT INTO event (ts, topic, payload) VALUES (?, ?, ?)
ON CONFLICT (topic, ts) DO NOTHING
"""


def _clean(value: Optional[float]) -> Optional[float]:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def _statements(sql: str) -> List[str]:
    stripped = " ".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [s.strip() for s in stripped.split(";") if s.strip()]


class Store:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def migrate(self, verbose: bool = True) -> int:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version    INTEGER NOT NULL,
                applied_ts INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                PRIMARY KEY (version)
            )""")
        done = {r["version"] for r in self.conn.execute("SELECT version FROM schema_version")}
        applied = 0
        for name, sql in SCHEMA:
            version = int(name.split("_", 1)[0])
            if version in done:
                continue
            self.conn.execute("BEGIN")
            try:
                for stmt in _statements(sql):
                    self.conn.execute(stmt)
                self.conn.execute("INSERT INTO schema_version (version, applied_ts, name) "
                                  "VALUES (?, strftime('%s','now'), ?)", (version, name))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            if verbose:
                print(f"  migrated {name}")
            applied += 1
        return applied

    def add_reading(self, ts: int, sensor: str, value: Optional[float], unit: str,
                    valid: bool, note: str = "") -> bool:
        cur = self.conn.execute(INSERT_READING, (int(ts), sensor, _clean(value), unit,
                                                 1 if valid else 0, note or ""))
        return cur.rowcount > 0

    def add_readings(self, rows: Iterable[Sequence]) -> Tuple[int, int]:
        rows = [(int(ts), sensor, _clean(value), unit, 1 if valid else 0, note or "")
                for ts, sensor, value, unit, valid, note in rows]
        if not rows:
            return 0, 0
        before = self.conn.total_changes
        self.conn.execute("BEGIN")
        try:
            self.conn.executemany(INSERT_READING, rows)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        inserted = self.conn.total_changes - before
        return inserted, len(rows) - inserted

    def add_event(self, ts: int, topic: str, payload: str) -> bool:
        return self.conn.execute(INSERT_EVENT, (int(ts), topic, payload)).rowcount > 0

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM reading").fetchone()[0]

    def latest(self) -> List[sqlite3.Row]:
        return list(self.conn.execute("""
            SELECT r.* FROM reading r
            JOIN (SELECT sensor, MAX(ts) AS ts FROM reading GROUP BY sensor) m
              ON r.sensor = m.sensor AND r.ts = m.ts
            ORDER BY r.sensor"""))

    def history(self, sensor: str, since_ts: int, limit: int = 5000) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM reading WHERE sensor = ? AND ts >= ? ORDER BY ts LIMIT ?",
            (sensor, since_ts, limit)))

    def faults(self, limit: int = 50) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM reading WHERE valid = 0 ORDER BY ts DESC LIMIT ?", (limit,)))

    def summary(self) -> List[sqlite3.Row]:
        return list(self.conn.execute("""
            SELECT sensor, COUNT(*) AS n,
                   SUM(CASE WHEN valid=0 THEN 1 ELSE 0 END) AS bad,
                   MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                   ROUND(AVG(CASE WHEN valid=1 THEN value END), 2) AS avg_value
            FROM reading GROUP BY sensor ORDER BY sensor"""))

    def latest_event(self, topic: str):
        return self.conn.execute("SELECT * FROM event WHERE topic = ? ORDER BY ts DESC LIMIT 1",
                                 (topic,)).fetchone()

    def events_since(self, since_ts: int, limit: int = 500):
        return list(self.conn.execute(
            "SELECT * FROM event WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (since_ts, limit)))

    def series(self, sensor: str, since_ts: int, buckets: int = 60):
        row = self.conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM reading WHERE sensor = ? AND ts >= ? AND valid = 1",
            (sensor, since_ts)).fetchone()
        if not row or row[0] is None:
            return []
        width = max(1, (row[1] - row[0]) // buckets)
        return list(self.conn.execute(
            "SELECT (ts / ?) * ? AS bucket, AVG(value) AS v FROM reading "
            "WHERE sensor = ? AND ts >= ? AND valid = 1 GROUP BY bucket ORDER BY bucket",
            (width, width, sensor, since_ts)))

    def close(self) -> None:
        self.conn.close()


FLUSH_EVERY_S = 2.0
FLUSH_AT_ROWS = 200

_running = True
_q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)
stats = {"readings": 0, "duplicates": 0, "events": 0, "rejected": 0, "dropped": 0}


def _stop(_signum, _frame):
    global _running
    _running = False


def parse_reading(topic: str, payload: str) -> Optional[Tuple]:
    sensor = topic[len(SENSOR_PREFIX):]
    if not sensor:
        return None
    try:
        body = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    ts, unit, valid = body.get("timestamp"), body.get("unit"), body.get("valid")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    if not isinstance(unit, str) or not isinstance(valid, bool):
        return None
    value = body.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return None
    if valid and value is None:
        return None
    return (int(ts), sensor, value, unit, valid, str(body.get("note", "")))


def drain(store: Store, verbose: bool) -> None:
    rows: List[Tuple] = []
    events: List[Tuple] = []
    while True:
        try:
            kind, item = _q.get_nowait()
        except queue.Empty:
            break
        (rows if kind == "r" else events).append(item)
    if rows:
        inserted, dupes = store.add_readings(rows)
        stats["readings"] += inserted
        stats["duplicates"] += dupes
        if verbose:
            for ts, sensor, value, unit, valid, note in rows:
                print(f"  {sensor:<14} {value} {unit}{'' if valid else '  INVALID ' + note}")
    for ts, topic, payload in events:
        if store.add_event(ts, topic, payload):
            stats["events"] += 1


def cmd_ingest(args) -> int:
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("paho-mqtt is not installed. pip install -r requirements.txt", file=sys.stderr)
        return 1
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    store = Store(args.db)
    store.migrate()
    print(f"ingest -> {args.db}  ({store.count()} rows already)")

    def on_connect(client, _u, _f, reason_code, *_a):
        ok = getattr(reason_code, "is_failure", None)
        if not ((not ok()) if callable(ok) else (reason_code == 0)):
            print(f"  connect refused: {reason_code}")
            return
        print(f"  connected to {args.broker}:{args.port}")
        client.subscribe([(SENSOR_PREFIX + "#", 1), (STATE_PREFIX + "#", 1)])

    def on_message(_client, _u, msg):
        payload = msg.payload.decode("utf-8", "replace")
        if msg.topic.startswith(SENSOR_PREFIX):
            row = parse_reading(msg.topic, payload)
            if row is None:
                stats["rejected"] += 1
                print(f"  rejected {msg.topic}: {payload[:90]}")
                return
            item = ("r", row)
        elif msg.topic.startswith(STATE_PREFIX):
            try:
                ts = int(json.loads(payload).get("timestamp", time.time()))
            except Exception:
                ts = int(time.time())
            item = ("e", (ts, msg.topic, payload))
        else:
            return
        try:
            _q.put_nowait(item)
        except queue.Full:
            stats["dropped"] += 1

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hydro-ingest")
    except AttributeError:
        client = mqtt.Client(client_id="hydro-ingest")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect_async(args.broker, args.port, 60)
        client.loop_start()
    except Exception as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    last_flush = last_report = time.time()
    try:
        while _running:
            time.sleep(0.2)
            now = time.time()
            if now - last_flush >= FLUSH_EVERY_S or _q.qsize() >= FLUSH_AT_ROWS:
                drain(store, args.verbose)
                last_flush = now
            if now - last_report >= 60:
                print(f"  [{time.strftime('%H:%M:%S')}] {stats['readings']} rows, "
                      f"{stats['duplicates']} dupes, {stats['events']} events, "
                      f"{stats['rejected']} rejected"
                      + (f", {stats['dropped']} DROPPED" if stats['dropped'] else ""))
                last_report = now
    finally:
        client.loop_stop()
        client.disconnect()
        drain(store, args.verbose)
        print(f"\nstopped. {stats['readings']} rows written, {stats['duplicates']} duplicates "
              f"ignored, {stats['events']} events, {stats['rejected']} rejected. "
              f"{store.count()} in the table.")
        store.close()
    return 0


CSV_COLUMNS = ["ts", "iso", "sensor", "value", "unit", "valid", "note"]


def load_file(store: Store, path: str) -> tuple:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != CSV_COLUMNS:
            return 0, 0, f"unexpected columns: {reader.fieldnames}"
        rows, skipped = [], 0
        for line in reader:
            try:
                raw = line["value"].strip()
                rows.append((int(line["ts"]), line["sensor"],
                             float(raw) if raw else None,
                             line["unit"], line["valid"] == "1", line["note"]))
            except (ValueError, KeyError, TypeError):
                skipped += 1
    inserted, dupes = store.add_readings(rows)
    return inserted, dupes, (f"{skipped} malformed" if skipped else "")


def cmd_backfill(args) -> int:
    if os.path.isdir(args.path):
        files = sorted(glob.glob(os.path.join(args.path, "readings-*.csv")))
    else:
        files = [args.path]
    if not files:
        print(f"no csv found in {args.path}", file=sys.stderr)
        return 1
    store = Store(args.db)
    store.migrate()
    total_in = total_dupe = 0
    for path in files:
        inserted, dupes, note = load_file(store, path)
        total_in += inserted
        total_dupe += dupes
        print(f"  {os.path.basename(path):<28} +{inserted:<6} {dupes} already there"
              f"{'  (' + note + ')' if note else ''}")
    print(f"\n{total_in} rows imported, {total_dupe} skipped as duplicates")
    print(f"{store.count()} rows in the table")
    store.close()
    return 0


def _iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def cmd_report(args) -> int:
    store = Store(args.db)
    store.migrate(verbose=False)
    try:
        if args.history:
            rows = store.history(args.history, int(time.time() - args.hours * 3600))
            print(f"{args.history}, last {args.hours:g}h, {len(rows)} rows\n")
            for r in rows:
                val = "-" if r["value"] is None else f'{r["value"]:g}'
                print(f"  {_iso(r['ts'])}  {val:>10} {r['unit']}"
                      f"{'' if r['valid'] else '   INVALID ' + r['note']}")
            return 0
        if args.faults:
            rows = store.faults()
            print("no failed readings on record" if not rows else f"last {len(rows)} failed readings\n")
            for r in rows:
                print(f"  {_iso(r['ts'])}  {r['sensor']:<14} {r['note']}")
            return 0
        latest = store.latest()
        if not latest:
            print("table is empty")
            return 0
        print("latest\n")
        for r in latest:
            val = "-" if r["value"] is None else f'{r["value"]:g}'
            print(f"  {r['sensor']:<14} {val:>10} {r['unit']:<6} {int(time.time() - r['ts']):>6}s ago"
                  f"{'' if r['valid'] else '  INVALID'}")
        print(f"\nsummary\n\n  {'sensor':<14} {'rows':>7} {'bad':>5} {'avg':>10}   span")
        for r in store.summary():
            avg = "-" if r["avg_value"] is None else f'{r["avg_value"]:g}'
            print(f"  {r['sensor']:<14} {r['n']:>7} {r['bad']:>5} {avg:>10}   "
                  f"{_iso(r['first_ts'])} .. {_iso(r['last_ts'])}")
        print(f"\n  {store.count()} rows total in {args.db}")
        return 0
    finally:
        store.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="the hydro database")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=DB_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", parents=[common], help="subscribe to the broker and write rows")
    p.add_argument("--broker", default=MQTT_HOST)
    p.add_argument("--port", type=int, default=MQTT_PORT)
    p.add_argument("--verbose", action="store_true")
    p = sub.add_parser("backfill", parents=[common], help="load csv files from the control loop")
    p.add_argument("path", help="a csv file, or a directory of them")
    p = sub.add_parser("report", parents=[common], help="what is in the database")
    p.add_argument("--faults", action="store_true")
    p.add_argument("--history", metavar="SENSOR")
    p.add_argument("--hours", type=float, default=24.0)
    args = ap.parse_args()
    return {"ingest": cmd_ingest, "backfill": cmd_backfill, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
