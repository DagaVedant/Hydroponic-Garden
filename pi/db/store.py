"""Everything that touches the database.

One place, so the ingest service, the backfill tool and the dashboard all agree
about what a row looks like. Nothing else opens the file.
"""

from __future__ import annotations

import math
import os
import sqlite3
from typing import Iterable, List, Optional, Sequence, Tuple

DB_PATH = os.path.join("db", "hydro.sqlite")
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")

# ON CONFLICT DO NOTHING is the idempotency. mqtt qos 1 is at-least-once and every
# topic is retained, so a reconnecting subscriber gets handed the last value of
# everything again. Replays land on the (sensor, ts) key and are discarded.
INSERT_READING = """
INSERT INTO reading (ts, sensor, value, unit, valid, note)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (sensor, ts) DO NOTHING
"""

INSERT_EVENT = """
INSERT INTO event (ts, topic, payload)
VALUES (?, ?, ?)
ON CONFLICT (topic, ts) DO NOTHING
"""


def _clean(value: Optional[float]) -> Optional[float]:
    """NaN and infinity become NULL. sqlite would do the NaN part silently."""
    if value is None:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def _statements(sql: str) -> List[str]:
    """Split a migration into statements, dropping -- comments.

    Naive on purpose. These files are ours and contain no semicolons inside string
    literals; if that ever stops being true this needs a real parser rather than a
    subtle bug.
    """
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
        # WAL so the dashboard can read while ingest writes. Without it a reader
        # blocks the writer and the loop starts backing up.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------------ schema

    def migrate(self, verbose: bool = True) -> int:
        """Apply any migration not yet recorded. Safe to run every startup."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version    INTEGER NOT NULL,
                applied_ts INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                PRIMARY KEY (version)
            )""")
        done = {r["version"] for r in
                self.conn.execute("SELECT version FROM schema_version")}

        files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))
        applied = 0
        for fname in files:
            version = int(fname.split("_", 1)[0])
            if version in done:
                continue
            with open(os.path.join(MIGRATIONS_DIR, fname), encoding="utf-8") as fh:
                sql = fh.read()
            # each migration is one transaction. a half applied schema is worse
            # than an unapplied one.
            #
            # statements are run one at a time rather than through executescript,
            # because executescript implicitly commits any open transaction before
            # it starts, which would silently defeat the BEGIN below
            self.conn.execute("BEGIN")
            try:
                for stmt in _statements(sql):
                    self.conn.execute(stmt)
                self.conn.execute(
                    "INSERT INTO schema_version (version, applied_ts, name) "
                    "VALUES (?, strftime('%s','now'), ?)", (version, fname))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            if verbose:
                print(f"  migrated {fname}")
            applied += 1
        return applied

    # ------------------------------------------------------------------ writes

    def add_reading(self, ts: int, sensor: str, value: Optional[float],
                    unit: str, valid: bool, note: str = "") -> bool:
        cur = self.conn.execute(INSERT_READING,
                                (int(ts), sensor, _clean(value), unit,
                                 1 if valid else 0, note or ""))
        return cur.rowcount > 0          # False means it was a duplicate

    def add_readings(self, rows: Iterable[Sequence]) -> Tuple[int, int]:
        """Bulk insert. Returns (inserted, skipped_as_duplicate)."""
        rows = [(int(ts), sensor, _clean(value), unit, 1 if valid else 0, note or "")
                for ts, sensor, value, unit, valid, note in rows]
        if not rows:
            return 0, 0
        # total_changes is a counter sqlite already maintains, so this is O(1).
        # COUNT(*) would be a full table scan, twice, on every flush -- two seconds
        # apart forever, against a table that grows by 8600 rows a day.
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
        cur = self.conn.execute(INSERT_EVENT, (int(ts), topic, payload))
        return cur.rowcount > 0

    # ------------------------------------------------------------------ reads

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM reading").fetchone()[0]

    def latest(self) -> List[sqlite3.Row]:
        """Most recent reading per sensor. What a dashboard opens with."""
        return list(self.conn.execute("""
            SELECT r.* FROM reading r
            JOIN (SELECT sensor, MAX(ts) AS ts FROM reading GROUP BY sensor) m
              ON r.sensor = m.sensor AND r.ts = m.ts
            ORDER BY r.sensor
        """))

    def history(self, sensor: str, since_ts: int, limit: int = 5000) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM reading WHERE sensor = ? AND ts >= ? "
            "ORDER BY ts LIMIT ?", (sensor, since_ts, limit)))

    def faults(self, limit: int = 50) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM reading WHERE valid = 0 ORDER BY ts DESC LIMIT ?", (limit,)))

    def summary(self) -> List[sqlite3.Row]:
        return list(self.conn.execute("""
            SELECT sensor,
                   COUNT(*)                      AS n,
                   SUM(CASE WHEN valid=0 THEN 1 ELSE 0 END) AS bad,
                   MIN(ts) AS first_ts,
                   MAX(ts) AS last_ts,
                   ROUND(AVG(CASE WHEN valid=1 THEN value END), 2) AS avg_value
            FROM reading GROUP BY sensor ORDER BY sensor
        """))

    def latest_event(self, topic: str):
        """Most recent payload on a state topic. lights and dosing read this."""
        return self.conn.execute(
            "SELECT * FROM event WHERE topic = ? ORDER BY ts DESC LIMIT 1",
            (topic,)).fetchone()

    def events_since(self, since_ts: int, limit: int = 500):
        return list(self.conn.execute(
            "SELECT * FROM event WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (since_ts, limit)))

    def series(self, sensor: str, since_ts: int, buckets: int = 60):
        """Downsampled history for a sparkline.

        Averaging into buckets rather than returning every row. A day of readings
        at 60s is 1440 points per sensor, and a 120px sparkline cannot show them.
        Sending them all would just be moving work to the browser to throw away.
        """
        row = self.conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM reading WHERE sensor = ? AND ts >= ? "
            "AND valid = 1", (sensor, since_ts)).fetchone()
        if not row or row[0] is None:
            return []
        lo, hi = row[0], row[1]
        width = max(1, (hi - lo) // buckets)
        return list(self.conn.execute(
            "SELECT (ts / ?) * ? AS bucket, AVG(value) AS v "
            "FROM reading WHERE sensor = ? AND ts >= ? AND valid = 1 "
            "GROUP BY bucket ORDER BY bucket", (width, width, sensor, since_ts)))

    def close(self) -> None:
        self.conn.close()
