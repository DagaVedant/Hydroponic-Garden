"""Import the control service's csv files into sqlite.

    python -m ingest.backfill data/
    python -m ingest.backfill data/readings-2026-09-02.csv
    python -m ingest.backfill data/ --db db/hydro.sqlite

The csv columns were chosen to match the reading table, so this is a load and not
a transformation. That was the point of picking them that way.

Safe to run twice. The (sensor, ts) primary key drops anything already present, so
re-importing the same day adds nothing.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys

sys.path.insert(0, ".")

from db.store import DB_PATH, Store           # noqa: E402

EXPECTED = ["ts", "iso", "sensor", "value", "unit", "valid", "note"]


def load_file(store: Store, path: str) -> tuple:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != EXPECTED:
            return 0, 0, f"unexpected columns: {reader.fieldnames}"

        rows, skipped = [], 0
        for line in reader:
            try:
                raw = line["value"].strip()
                rows.append((
                    int(line["ts"]),
                    line["sensor"],
                    float(raw) if raw else None,     # blank means the reading failed
                    line["unit"],
                    line["valid"] == "1",
                    line["note"],
                ))
            except (ValueError, KeyError, TypeError):
                skipped += 1

    inserted, dupes = store.add_readings(rows)
    note = f"{skipped} malformed" if skipped else ""
    return inserted, dupes, note


def main() -> int:
    ap = argparse.ArgumentParser(description="csv -> sqlite")
    ap.add_argument("path", help="a csv file, or a directory of them")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

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
        extra = f"  ({note})" if note else ""
        print(f"  {os.path.basename(path):<28} +{inserted:<6} {dupes} already there{extra}")

    print(f"\n{total_in} rows imported, {total_dupe} skipped as duplicates")
    print(f"{store.count()} rows in the table")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
