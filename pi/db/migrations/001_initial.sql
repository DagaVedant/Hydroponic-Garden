-- 001  the reading table and the event log
--
-- Written so postgres is a swap and not a rewrite: no sqlite-only types, no
-- AUTOINCREMENT, no implicit rowid. On postgres, ts becomes BIGINT and valid
-- becomes BOOLEAN; nothing else changes.

CREATE TABLE IF NOT EXISTS reading (
    ts      INTEGER NOT NULL,          -- unix epoch seconds, utc
    sensor  TEXT    NOT NULL,          -- matches the mqtt topic suffix, e.g. water/temp
    value   REAL,                      -- NULL when the reading failed. see below
    unit    TEXT    NOT NULL,
    valid   INTEGER NOT NULL,          -- 0/1
    note    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (sensor, ts)
);

-- value is nullable on purpose, and the spec draft had it NOT NULL.
--
-- An invalid reading has no value by definition, and sqlite silently converts a
-- stored NaN to NULL anyway, so NOT NULL would have rejected every fault row. The
-- point of keeping invalid rows is that a gap in the data looks identical to "the
-- pi was off", where an explicit valid=0 row says the probe was failing.

-- The primary key is (sensor, ts) rather than a surrogate id. That is what makes
-- ingest idempotent: mqtt qos 1 is *at least once*, and every message is retained,
-- so a reconnecting subscriber is handed the last value of every topic again. With
-- this key a replay is a no-op instead of a duplicate row.

CREATE INDEX IF NOT EXISTS reading_sensor_ts ON reading (sensor, ts);
CREATE INDEX IF NOT EXISTS reading_ts        ON reading (ts);

-- Invalid rows get their own index. The fault log wants "what has been failing"
-- and without this it is a full scan of a table that is 99% healthy rows.
CREATE INDEX IF NOT EXISTS reading_invalid ON reading (ts) WHERE valid = 0;


CREATE TABLE IF NOT EXISTS event (
    ts      INTEGER NOT NULL,
    topic   TEXT    NOT NULL,          -- hydro/state/online, hydro/state/fault
    payload TEXT    NOT NULL,          -- raw json, kept verbatim
    PRIMARY KEY (topic, ts)
);

CREATE INDEX IF NOT EXISTS event_ts ON event (ts);
