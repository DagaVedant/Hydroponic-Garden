"""Look at what is in the database without writing sql by hand.

    python -m db.report                 latest value of everything, plus a summary
    python -m db.report --faults        what has been failing
    python -m db.report --history water/temp --hours 24

A stand-in for the dashboard until the dashboard exists.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

sys.path.insert(0, ".")

from db.store import DB_PATH, Store           # noqa: E402


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser(description="read the hydro database")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--faults", action="store_true")
    ap.add_argument("--history", metavar="SENSOR")
    ap.add_argument("--hours", type=float, default=24.0)
    args = ap.parse_args()

    store = Store(args.db)

    if args.history:
        since = int(time.time() - args.hours * 3600)
        rows = store.history(args.history, since)
        print(f"{args.history}, last {args.hours:g}h, {len(rows)} rows\n")
        for r in rows:
            mark = "" if r["valid"] else "   INVALID " + r["note"]
            val = "-" if r["value"] is None else f'{r["value"]:g}'
            print(f"  {iso(r['ts'])}  {val:>10} {r['unit']}{mark}")
        store.close()
        return 0

    if args.faults:
        rows = store.faults()
        if not rows:
            print("no failed readings on record")
        else:
            print(f"last {len(rows)} failed readings\n")
            for r in rows:
                print(f"  {iso(r['ts'])}  {r['sensor']:<14} {r['note']}")
        store.close()
        return 0

    latest = store.latest()
    if not latest:
        print("table is empty")
        store.close()
        return 0

    print("latest\n")
    for r in latest:
        age = int(time.time() - r["ts"])
        val = "-" if r["value"] is None else f'{r["value"]:g}'
        flag = "" if r["valid"] else "  INVALID"
        print(f"  {r['sensor']:<14} {val:>10} {r['unit']:<6} {age:>6}s ago{flag}")

    print("\nsummary\n")
    print(f"  {'sensor':<14} {'rows':>7} {'bad':>5} {'avg':>10}   span")
    for r in store.summary():
        span = f"{iso(r['first_ts'])} .. {iso(r['last_ts'])}"
        avg = "-" if r["avg_value"] is None else f'{r["avg_value"]:g}'
        print(f"  {r['sensor']:<14} {r['n']:>7} {r['bad']:>5} {avg:>10}   {span}")

    print(f"\n  {store.count()} rows total in {args.db}")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
