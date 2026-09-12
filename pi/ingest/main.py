"""Subscribe to the broker, validate, write to sqlite.

    python -m ingest.main                    run it
    python -m ingest.main --db somewhere.db  different file
    python -m ingest.main --verbose          print every row

Runs alongside the control service, not inside it. That separation is the reason
mqtt is here at all on a single machine: the control loop should not stop reading
sensors because the database is locked, and the database should not miss a day
because the control loop crashed.

Validation happens here as well as in the control service. The broker is not
trusted, because anything can publish to it -- a test script, a half finished
dashboard, a future board someone bolts on. A malformed payload is logged and
dropped, never written.

**The network callback never touches the database.** paho calls on_message from its
own thread, and a sqlite connection belongs to the thread that opened it. Messages
are parsed and queued there; the main thread owns the database and drains the queue
in batches. That fixes the threading rule and makes writes an order of magnitude
cheaper than a transaction per row.
"""

from __future__ import annotations

import argparse
import json
import queue
import signal
import sys
import time
from typing import List, Optional, Tuple

sys.path.insert(0, ".")                       # so `db` resolves when run from pi/

from db.store import DB_PATH, Store           # noqa: E402

TOPIC_SENSOR = "hydro/sensor/#"
TOPIC_STATE = "hydro/state/#"
SENSOR_PREFIX = "hydro/sensor/"
STATE_PREFIX = "hydro/state/"

FLUSH_EVERY_S = 2.0
FLUSH_AT_ROWS = 200

_running = True
_q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)
stats = {"readings": 0, "duplicates": 0, "events": 0, "rejected": 0, "dropped": 0}


def _stop(_signum, _frame):
    global _running
    _running = False


def parse_reading(topic: str, payload: str) -> Optional[Tuple]:
    """Turn a message into a row, or None if it is not one.

    Everything here is a rejection reason, not a repair. A payload that is nearly
    right is still wrong, and guessing at what it meant is how bad data gets into
    a table that is supposed to be the record.
    """
    sensor = topic[len(SENSOR_PREFIX):]
    if not sensor:
        return None

    try:
        body = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None

    ts = body.get("timestamp")
    unit = body.get("unit")
    valid = body.get("valid")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    if not isinstance(unit, str) or not isinstance(valid, bool):
        return None

    value = body.get("value")
    if value is not None and (isinstance(value, bool)
                              or not isinstance(value, (int, float))):
        return None
    # a reading claiming to be valid with no number is contradictory
    if valid and value is None:
        return None

    return (int(ts), sensor, value, unit, valid, str(body.get("note", "")))


def drain(store: Store, verbose: bool) -> None:
    """Move everything queued into the database in one transaction."""
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
                flag = "" if valid else "  INVALID " + note
                print(f"  {sensor:<14} {value} {unit}{flag}")

    for ts, topic, payload in events:
        if store.add_event(ts, topic, payload):
            stats["events"] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description="mqtt -> sqlite")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("paho-mqtt is not installed. pip install -r ingest/requirements.txt",
              file=sys.stderr)
        return 1

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    store = Store(args.db)
    store.migrate()
    print(f"ingest -> {args.db}  ({store.count()} rows already)")

    # ---------------------------------------------------------- mqtt callbacks
    # these run on paho's thread. parse and queue only, no database, no disk

    def on_connect(client, _u, _f, reason_code, *_a):
        ok = getattr(reason_code, "is_failure", None)
        good = (not ok()) if callable(ok) else (reason_code == 0)
        if not good:
            print(f"  connect refused: {reason_code}")
            return
        print(f"  connected to {args.broker}:{args.port}")
        client.subscribe([(TOPIC_SENSOR, 1), (TOPIC_STATE, 1)])
        # retained messages arrive immediately on subscribe. they are usually rows
        # we already have; the (sensor, ts) key drops them without complaint

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
            except Exception:                              # noqa: BLE001
                ts = int(time.time())
            item = ("e", (ts, msg.topic, payload))
        else:
            return

        try:
            _q.put_nowait(item)
        except queue.Full:
            # the writer has fallen behind badly. drop and say so rather than
            # blocking paho's network thread, which would stall the connection
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
    except Exception as exc:                               # noqa: BLE001
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
                print(f"  [{time.strftime('%H:%M:%S')}] "
                      f"{stats['readings']} rows, {stats['duplicates']} dupes, "
                      f"{stats['events']} events, {stats['rejected']} rejected"
                      + (f", {stats['dropped']} DROPPED" if stats['dropped'] else ""))
                last_report = now
    finally:
        client.loop_stop()
        client.disconnect()
        drain(store, args.verbose)             # do not lose what is still queued
        print(f"\nstopped. {stats['readings']} rows written, "
              f"{stats['duplicates']} duplicates ignored, "
              f"{stats['events']} events, {stats['rejected']} rejected. "
              f"{store.count()} in the table.")
        store.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
