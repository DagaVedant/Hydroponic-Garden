"""Ingest, storage, the dashboard and the alerts, all against temporary files."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                            # noqa: E402
from control import CsvLogger, Reading                                   # noqa: E402
from store import Store, load_file, parse_reading                        # noqa: E402
from web import EVENTS, Rules                                            # noqa: E402


class IngestTests(unittest.TestCase):
    def test_parse_reading(self):
        ok = parse_reading("hydro/sensor/water/temp",
                           '{"value": 20.5, "unit": "C", "timestamp": 1700000000, "valid": true}')
        self.assertEqual(ok, (1700000000, "water/temp", 20.5, "C", True, ""))

        bad = parse_reading("hydro/sensor/water/ph",
                            '{"value": null, "unit": "pH", "timestamp": 1700000000, '
                            '"valid": false, "note": "ads1115 read failed"}')
        self.assertEqual(bad[2], None)
        self.assertEqual(bad[5], "ads1115 read failed")

        for payload in ("not json", "[1,2]", '{"value": 1}',
                        '{"value": "1", "unit": "C", "timestamp": 1, "valid": true}',
                        '{"value": null, "unit": "C", "timestamp": 1, "valid": true}',
                        '{"value": 1, "unit": "C", "timestamp": true, "valid": true}'):
            self.assertIsNone(parse_reading("hydro/sensor/x", payload), payload)
        self.assertIsNone(parse_reading("hydro/sensor/", '{"value": 1, "unit": "C", '
                                        '"timestamp": 1, "valid": true}'))

    def test_store_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(os.path.join(d, "t.sqlite"))
            s.migrate(verbose=False)
            rows = [(1700000000, "water/temp", 20.5, "C", True, ""),
                    (1700000000, "water/ph", None, "pH", False, "failed"),
                    (1700000060, "water/temp", float("nan"), "C", False, "nan")]
            self.assertEqual(s.add_readings(rows), (3, 0))
            self.assertEqual(s.add_readings(rows), (0, 3))
            self.assertEqual(s.count(), 3)
            latest = {r["sensor"]: r for r in s.latest()}
            self.assertIsNone(latest["water/temp"]["value"])      # nan stored as null
            self.assertEqual(len(s.faults()), 2)
            self.assertEqual(s.migrate(verbose=False), 0)
            s.close()

    def test_backfill_matches_the_logger(self):
        with tempfile.TemporaryDirectory() as d:
            log = CsvLogger(os.path.join(d, "data"))
            log.write([Reading("water/temp", 20.5, "C", True, ts=1700000000.4),
                       Reading.bad("water/ph", "pH", "ads1115 read failed")])
            s = Store(os.path.join(d, "t.sqlite"))
            s.migrate(verbose=False)
            inserted, dupes, note = load_file(s, log.path)
            self.assertEqual((inserted, dupes, note), (2, 0, ""))
            inserted, dupes, note = load_file(s, log.path)
            self.assertEqual((inserted, dupes), (0, 2))
            rows = {r["sensor"]: r for r in s.latest()}
            self.assertEqual(rows["water/temp"]["ts"], 1700000000)
            self.assertEqual(rows["water/ph"]["note"], "ads1115 read failed")
            self.assertIsNone(rows["water/ph"]["value"])
            s.close()


class DashboardTests(unittest.TestCase):
    def setUp(self):
        import web as W
        self.W = W
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "demo.sqlite")
        W.seed_demo(self.db)
        W.app.config["db_path"] = self.db
        self.client = W.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_page_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"api/snapshot", r.data)
        self.assertEqual(self.client.get("/tower.png").status_code, 200)

    def test_snapshot(self):
        snap = self.client.get("/api/snapshot").get_json()
        self.assertEqual([s["sensor"] for s in snap["sensors"]],
                         [k for k, *_ in self.W.SENSORS])
        for s in snap["sensors"]:
            self.assertTrue(s["valid"], s)
            self.assertIn(s["status"], ("ok", "warn"))
            self.assertGreater(len(s["series"]), 10)
        self.assertTrue(snap["online"])
        self.assertEqual(snap["dosing"]["state"], "idle")
        self.assertEqual(snap["tank_full_l"], self.W.TANK_FULL_L)
        self.assertEqual(snap["alerts"], [])
        # the demo tank sits inside the level band the config draws
        lvl = next(s for s in snap["sensors"] if s["sensor"] == "water/level")
        self.assertEqual(lvl["status"], "ok", lvl)

    def test_bands_come_from_config(self):
        self.assertEqual(self.W.LABELS["water/ph"][2:], config.BANDS["water/ph"])

    def test_timeline(self):
        tl = self.client.get("/api/timeline").get_json()
        kinds = {e["kind"] for e in tl["events"]}
        self.assertEqual(kinds, {"dose", "light", "fault"})
        self.assertEqual(tl["summary"]["doses_24h"], 5)
        doses = [e for e in tl["events"] if e["kind"] == "dose"]
        self.assertTrue(all("verified" in e["text"] for e in doses))

    def test_commands(self):
        r = self.client.post("/api/cmd/lights", json={"on": True})
        self.assertIn(r.status_code, (200, 503))
        self.assertEqual(self.client.post("/api/cmd/pump", json={}).status_code, 404)


class AlertTests(unittest.TestCase):
    T0 = 1_700_000_000.0

    def feed_ok(self, rules, ts, level=10.0):
        for sensor, value in (("water/level", level), ("water/ph", 5.8),
                              ("water/ec", 1200.0), ("water/temp", 21.0),
                              ("air/temp", 22.0), ("air/humidity", 55.0)):
            rules.feed_reading(sensor, value, True, ts)
        self.beat(rules, ts)

    @staticmethod
    def beat(rules, ts):
        """The control service is alive. Without this every test times out into
        an offline alert, which is the heartbeat rule working, not the one under
        test."""
        rules.feed_state("online", {"online": True, "timestamp": ts}, ts)

    def tick(self, rules, ts):
        self.beat(rules, ts)
        return rules.tick(ts)

    def test_quiet_when_healthy(self):
        r = Rules()
        self.feed_ok(r, self.T0)
        self.assertEqual(r.tick(self.T0), [])
        self.assertEqual(r.summary(self.T0)["count"], 0)

    def test_level_low_raises_and_clears(self):
        r = Rules()
        self.feed_ok(r, self.T0, level=config.ALERT_LEVEL_LOW_L - 0.5)
        out = self.tick(r, self.T0)
        self.assertEqual([(k, key) for k, key, _ in out], [("raised", "level_low")])
        self.assertEqual(self.tick(r, self.T0 + 60), [])               # no spam
        out = self.tick(r, self.T0 + config.ALERT_REPEAT_S + 1)
        self.assertEqual(out[0][:2], ("repeat", "level_low"))
        self.feed_ok(r, self.T0 + config.ALERT_REPEAT_S + 120, level=9.0)
        out = self.tick(r, self.T0 + config.ALERT_REPEAT_S + 120)
        self.assertEqual(out[0][:2], ("cleared", "level_low"))

    def test_level_rise_is_an_event(self):
        r = Rules()
        self.feed_ok(r, self.T0, level=9.0)
        self.assertEqual(r.tick(self.T0), [])
        # the tower drains back into the tank over a few minutes
        for i, lvl in enumerate((9.2, 9.6, 10.1, 10.4)):
            r.feed_reading("water/level", lvl, True, self.T0 + 60 * (i + 1))
        out = self.tick(r, self.T0 + 240)
        self.assertEqual(out[0][:2], ("raised", "level_rise"))
        self.assertIn("pump", out[0][2])
        # it slides out of the window without a "cleared" message
        self.assertIn("level_rise", EVENTS)
        later = self.T0 + config.ALERT_LEVEL_RISE_WINDOW_S + 600
        r.feed_reading("water/level", 10.4, True, later)
        self.assertEqual(self.tick(r, later), [])
        self.assertNotIn("level_rise", r.active)

    def test_out_of_band_needs_to_persist(self):
        r = Rules()
        self.feed_ok(r, self.T0)
        r.feed_reading("water/ph", 6.9, True, self.T0)
        self.assertEqual(self.tick(r, self.T0), [])
        self.assertEqual(self.tick(r, self.T0 + config.ALERT_OUT_OF_BAND_S - 1), [])
        r.feed_reading("water/ph", 6.9, True, self.T0 + config.ALERT_OUT_OF_BAND_S)
        out = self.tick(r, self.T0 + config.ALERT_OUT_OF_BAND_S)
        self.assertEqual(out[0][:2], ("raised", "band:water/ph"))
        self.assertIn("pH is 6.9", out[0][2])
        # one good reading resets the clock
        r.feed_reading("water/ph", 5.8, True, self.T0 + config.ALERT_OUT_OF_BAND_S + 60)
        out = self.tick(r, self.T0 + config.ALERT_OUT_OF_BAND_S + 60)
        self.assertEqual(out[0][:2], ("cleared", "band:water/ph"))

    def test_sensor_failing(self):
        r = Rules()
        self.feed_ok(r, self.T0)
        r.feed_reading("water/ec", None, False, self.T0)
        self.assertEqual(self.tick(r, self.T0 + 60), [])
        out = self.tick(r, self.T0 + config.ALERT_SENSOR_FAIL_S)
        self.assertEqual(out[0][:2], ("raised", "fail:water/ec"))

    def test_dosing_fault(self):
        r = Rules()
        self.feed_ok(r, self.T0)
        r.feed_state("dosing", {"state": "fault", "fault": "micro dose did not land"}, self.T0)
        out = r.tick(self.T0)
        self.assertEqual(out[0][:2], ("raised", "dosing_fault"))
        self.assertIn("did not land", out[0][2])
        r.feed_state("dosing", {"state": "idle", "fault": ""}, self.T0 + 60)
        self.assertEqual(r.tick(self.T0 + 60)[0][:2], ("cleared", "dosing_fault"))

    def test_offline_by_will_and_by_silence(self):
        r = Rules()
        self.feed_ok(r, self.T0)
        r.feed_state("online", {"online": False}, self.T0 + 10)
        self.assertEqual(r.tick(self.T0 + 10)[0][:2], ("raised", "offline"))
        r.feed_state("online", {"online": True, "timestamp": self.T0 + 20}, self.T0 + 20)
        self.assertEqual(r.tick(self.T0 + 20)[0][:2], ("cleared", "offline"))
        out = r.tick(self.T0 + 20 + config.ALERT_HEARTBEAT_S + 1)
        self.assertEqual(out[0][:2], ("raised", "offline"))
        self.assertIn("heartbeat", out[0][2])

    def test_summary_is_json(self):
        r = Rules()
        self.feed_ok(r, self.T0, level=1.0)
        r.tick(self.T0)
        body = json.loads(json.dumps(r.summary(self.T0)))
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["active"][0]["key"], "level_low")
        self.assertLessEqual(body["timestamp"], int(time.time()) + 1_000_000)


if __name__ == "__main__":
    unittest.main()
