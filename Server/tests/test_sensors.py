import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import bookings, main

KEY = {"X-Gateway-Key": "test-key"}


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "parking.db"
        for patcher in (
            patch.object(main, "DB_PATH", self.db_path),
            patch.dict(os.environ, {"PARKING_GATEWAY_KEY": "test-key"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        main._my_slot_hits.clear()

        main.init_db()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript(
                """
                INSERT INTO parkingslots VALUES ('slot-1', 'SLOT-001', 0, 0, 'B1');
                INSERT INTO parkingslots VALUES ('slot-2', 'SLOT-002', 0, 0, 'B1');
                """
            )
        self.client = TestClient(main.app)
        self.client.post(
            "/gateway/slot-map",
            json=[
                {"slot_id": "slot-1", "node": 1, "channel": 0},
                {"slot_id": "slot-2", "node": 1, "channel": 1},
            ],
            headers=KEY,
        )

    def query(self, sql, params=()):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def enter(self, plate="ABC123"):
        response = self.client.post("/gate-entry", json={"number_plate": plate})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def sense(self, occupied, **target):
        return self.client.post(
            "/sensor-events", json=[{**target, "occupied": occupied}], headers=KEY
        )

    # gateway auth

    def test_gateway_endpoints_reject_missing_or_wrong_key(self):
        body = [{"slot_id": "slot-1", "occupied": True}]
        self.assertEqual(self.client.post("/sensor-events", json=body).status_code, 401)
        wrong = {"X-Gateway-Key": "nope"}
        self.assertEqual(self.client.post("/sensor-events", json=body, headers=wrong).status_code, 401)

    def test_gateway_endpoints_unavailable_when_key_not_configured(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PARKING_GATEWAY_KEY")
            response = self.client.post("/sensor-events", json=[], headers=KEY)
        self.assertEqual(response.status_code, 503)

    def test_slot_map_rejects_duplicate_node_channel(self):
        response = self.client.post(
            "/gateway/slot-map",
            json=[{"slot_id": "slot-2", "node": 1, "channel": 0}],
            headers=KEY,
        )
        self.assertEqual(response.status_code, 409)

    # sensor events

    def test_assigned_car_arriving_becomes_parked(self):
        slot_id = self.enter()["parking_slot"]["id"]
        node = {"slot-1": 0, "slot-2": 1}[slot_id]

        response = self.sense(True, node=1, channel=node)

        self.assertEqual(response.json()["applied"][0]["outcome"], "parked")
        self.assertEqual(self.query("SELECT status FROM bookings")[0]["status"], "parked")
        slot = self.query("SELECT * FROM parkingslots WHERE id = ?", (slot_id,))[0]
        self.assertEqual((slot["digitalstatus"], slot["physicalstatus"]), (1, 1))

    def test_unbooked_car_raises_alert_and_clears_when_it_leaves(self):
        response = self.sense(True, slot_id="slot-2")
        self.assertEqual(response.json()["applied"][0]["outcome"], "unbooked")
        alerts = self.client.get("/alerts").json()
        self.assertEqual([a["type"] for a in alerts], ["unbooked_car"])
        self.assertEqual(alerts[0]["slotid"], "slot-2")

        self.sense(True, slot_id="slot-2")  # repeated reading must not duplicate
        self.assertEqual(len(self.client.get("/alerts").json()), 1)

        self.sense(False, slot_id="slot-2")
        self.assertEqual(self.client.get("/alerts").json(), [])

    def test_occupied_slot_is_not_assigned_at_gate(self):
        self.sense(True, slot_id="slot-1")
        self.assertEqual(self.enter()["parking_slot"]["id"], "slot-2")

    def test_unknown_slot_is_rejected_without_failing_the_batch(self):
        response = self.client.post(
            "/sensor-events",
            json=[
                {"node": 9, "channel": 9, "occupied": True},
                {"slot_id": "slot-1", "occupied": True},
            ],
            headers=KEY,
        )
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["rejected"][0]["index"], 0)
        self.assertEqual(body["applied"][0]["slot_id"], "slot-1")

    def test_alert_can_be_resolved_by_the_guard(self):
        self.sense(True, slot_id="slot-1")
        alert = self.client.get("/alerts").json()[0]

        self.assertEqual(self.client.post(f"/alerts/{alert['id']}/resolve").status_code, 200)
        self.assertEqual(self.client.get("/alerts").json(), [])
        self.assertEqual(len(self.client.get("/alerts?open=false").json()), 1)
        self.assertEqual(self.client.post(f"/alerts/{alert['id']}/resolve").status_code, 404)

    # node health

    def test_offline_node_blocks_assignment_and_raises_alert(self):
        self.client.post("/gateway/heartbeat", json={"nodes": [{"node": 1, "online": False}]}, headers=KEY)

        response = self.client.post("/gate-entry", json={"number_plate": "ABC123"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual([a["type"] for a in self.client.get("/alerts").json()], ["node_offline"])
        self.assertEqual(sum(f["free"] for f in self.client.get("/availability").json()), 0)

        self.client.post("/gateway/heartbeat", json={"nodes": [{"node": 1, "online": True}]}, headers=KEY)
        self.assertEqual(self.client.get("/alerts").json(), [])
        self.assertEqual(self.client.post("/gate-entry", json={"number_plate": "ABC123"}).status_code, 200)

    # expiry

    def test_unparked_booking_expires_after_window_and_frees_slot(self):
        entry = self.enter()
        slot_id = entry["parking_slot"]["id"]
        start = bookings.utcnow()

        with closing(main.get_db()) as conn, conn:
            self.assertEqual(bookings.expire_bookings(conn, start + timedelta(minutes=14)), [])
            expired = bookings.expire_bookings(conn, start + timedelta(minutes=16))

        self.assertEqual(len(expired), 1)
        self.assertEqual(self.query("SELECT status FROM bookings")[0]["status"], "noshow")
        self.assertEqual(self.query("SELECT digitalstatus FROM parkingslots WHERE id = ?", (slot_id,))[0]["digitalstatus"], 0)
        self.assertIsNotNone(self.query("SELECT endtime FROM logs")[0]["endtime"])
        self.assertEqual(self.client.post("/gate-entry", json={"number_plate": "ABC123"}).status_code, 200)

    def test_parked_booking_never_expires(self):
        slot_id = self.enter()["parking_slot"]["id"]
        self.sense(True, slot_id=slot_id)

        with closing(main.get_db()) as conn, conn:
            expired = bookings.expire_bookings(conn, bookings.utcnow() + timedelta(hours=3))

        self.assertEqual(expired, [])
        self.assertEqual(self.query("SELECT status FROM bookings")[0]["status"], "parked")

    def test_exit_completes_the_booking(self):
        slot_id = self.enter()["parking_slot"]["id"]
        self.sense(True, slot_id=slot_id)
        self.client.post("/gate-exit", json={"number_plate": "ABC123"})
        self.assertEqual(self.query("SELECT status FROM bookings")[0]["status"], "completed")

    # driver endpoints

    def test_my_slot_returns_slot_and_directions(self):
        slot_id = self.enter()["parking_slot"]["id"]

        body = self.client.get("/my-slot", params={"plate": " ABC123 "}).json()

        self.assertEqual(body["parking_slot"]["id"], slot_id)
        self.assertEqual(body["booking_status"], "assigned")
        self.assertIsNotNone(body["expires_at"])
        self.assertIn("floor B1", body["directions"])

    def test_my_slot_404_for_unknown_plate_or_no_active_booking(self):
        self.assertEqual(self.client.get("/my-slot", params={"plate": "NOPE"}).status_code, 404)
        self.enter()
        self.client.post("/gate-exit", json={"number_plate": "ABC123"})
        self.assertEqual(self.client.get("/my-slot", params={"plate": "ABC123"}).status_code, 404)

    def test_my_slot_is_rate_limited(self):
        with patch.object(main, "MY_SLOT_LIMIT_PER_MINUTE", 2):
            codes = [self.client.get("/my-slot", params={"plate": "X"}).status_code for _ in range(3)]
        self.assertEqual(codes, [404, 404, 429])

    def test_bookings_list_joins_user_and_slot_and_filters_by_active(self):
        self.enter("ABC123")
        self.enter("XYZ789")
        self.client.post("/gate-exit", json={"number_plate": "ABC123"})

        active = self.client.get("/bookings", params={"active": "true"}).json()
        done = self.client.get("/bookings", params={"active": "false"}).json()

        self.assertEqual([b["numberplate"] for b in active], ["XYZ789"])
        self.assertEqual(active[0]["floor"], "B1")
        self.assertEqual(active[0]["status"], "assigned")
        self.assertEqual([b["numberplate"] for b in done], ["ABC123"])
        self.assertEqual(len(self.client.get("/bookings").json()), 2)

    def test_cors_preflight_allows_gateway_key_header_for_browser_simulators(self):
        response = self.client.options(
            "/sensor-events",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-gateway-key",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("x-gateway-key", response.headers["access-control-allow-headers"].lower())

    def test_availability_counts_per_floor(self):
        self.enter()
        self.assertEqual(self.client.get("/availability").json(), [{"floor": "B1", "total": 2, "free": 1}])


if __name__ == "__main__":
    unittest.main()
