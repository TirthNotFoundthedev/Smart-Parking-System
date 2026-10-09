import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import main


class ServerBasicsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "parking.db"
        patcher = patch.object(main, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE parkingslots (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    digitalstatus BOOLEAN NOT NULL,
                    physicalstatus BOOLEAN NOT NULL, floor TEXT NOT NULL
                );
                CREATE TABLE logs (
                    id TEXT PRIMARY KEY, slotid TEXT NOT NULL,
                    starttime DATETIME NOT NULL, endtime DATETIME,
                    userid TEXT NOT NULL
                );
                CREATE TABLE users (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    numberplate TEXT NOT NULL, phonenumber TEXT NOT NULL
                );
                """
            )
        main.init_db()
        self.client = TestClient(main.app)
        login = self.client.post(
            "/guard/login",
            json={"username": main.GUARD_USERNAME, "password": main.GUARD_PASSWORD},
        )
        self.client.headers["Authorization"] = f"Bearer {login.json()['token']}"

    def add_slot(self, slot_id, name, floor):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO parkingslots VALUES (?, ?, 0, 0, ?)",
                (slot_id, name, floor),
            )

    def test_health_check(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_parking_slots_lists_every_row(self):
        self.add_slot("a", "SLOT-001", "B1")
        self.add_slot("b", "SLOT-002", "B2")

        response = self.client.get("/parking-slots")

        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.json()}, {"a", "b"})

    def test_parking_slots_empty_lot_returns_empty_list(self):
        self.assertEqual(self.client.get("/parking-slots").json(), [])

    def test_booking_prefers_lowest_floor_then_name_not_uuid_order(self):
        self.add_slot("zzz", "SLOT-001", "B1")
        self.add_slot("aaa", "SLOT-002", "B1")
        self.add_slot("mmm", "SLOT-000", "B2")

        response = self.client.post("/gate-entry", json={"number_plate": "ORD1"})

        self.assertEqual(response.json()["parking_slot"]["id"], "zzz")

    def test_cors_allows_any_origin_without_credentials(self):
        response = self.client.get("/health", headers={"Origin": "http://anywhere.example"})

        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")
        self.assertNotIn("access-control-allow-credentials", response.headers)

    def test_cors_preflight_allows_auth_headers_and_put(self):
        response = self.client.options(
            "/gate-entry",
            headers={
                "Origin": "http://anywhere.example",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "Authorization, X-Gateway-Key, Content-Type",
            },
        )

        self.assertEqual(response.status_code, 200)
        allowed = response.headers["access-control-allow-headers"].lower()
        for header in ("authorization", "x-gateway-key", "content-type"):
            self.assertIn(header, allowed)
        self.assertIn("PUT", response.headers["access-control-allow-methods"])

    # guard auth

    def anon(self):
        return TestClient(main.app)

    def test_guard_login_returns_token(self):
        response = self.anon().post(
            "/guard/login", json={"username": main.GUARD_USERNAME, "password": main.GUARD_PASSWORD}
        )

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.json()["token"]), 20)

    def test_guard_login_rejects_wrong_credentials(self):
        response = self.anon().post("/guard/login", json={"username": "user1234", "password": "nope"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Wrong username or password.")

    def test_guard_endpoints_require_login(self):
        anon = self.anon()
        calls = [
            anon.post("/gate-entry", json={"number_plate": "X1"}),
            anon.post("/gate-exit", json={"number_plate": "X1"}),
            anon.get("/logs"),
            anon.get("/bookings"),
            anon.get("/alerts"),
            anon.post("/alerts/abc/resolve"),
            anon.get("/logs", headers={"Authorization": "Bearer forged"}),
        ]

        for response in calls:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "Guard login required."})

    def test_public_endpoints_need_no_login(self):
        anon = self.anon()
        for path in ("/health", "/parking-slots", "/availability"):
            self.assertEqual(anon.get(path).status_code, 200)

    def test_gateway_key_works_for_guard_endpoints(self):
        self.add_slot("a", "SLOT-001", "B1")
        anon = self.anon()
        with patch.dict("os.environ", {"PARKING_GATEWAY_KEY": "k"}):
            ok = anon.post("/gate-entry", json={"number_plate": "GW1"}, headers={"X-Gateway-Key": "k"})
            bad = anon.get("/logs", headers={"X-Gateway-Key": "wrong"})

        self.assertEqual(ok.status_code, 200)
        self.assertEqual(bad.status_code, 401)

    # plates on slots and parking status

    def test_parking_slots_show_plate_of_assigned_slot_only(self):
        self.add_slot("a", "SLOT-001", "B1")
        self.add_slot("b", "SLOT-002", "B1")
        self.client.post("/gate-entry", json={"number_plate": "KA01AB1234"})

        rows = {row["id"]: row for row in self.client.get("/parking-slots").json()}

        self.assertEqual(rows["a"]["numberplate"], "KA01AB1234")
        self.assertEqual(rows["a"]["booking_status"], "assigned")
        self.assertIsNone(rows["b"]["numberplate"])
        self.assertIsNone(rows["b"]["booking_status"])

    def test_parking_slots_drop_plate_after_exit(self):
        self.add_slot("a", "SLOT-001", "B1")
        self.client.post("/gate-entry", json={"number_plate": "KA01AB1234"})
        self.client.post("/gate-exit", json={"number_plate": "KA01AB1234"})

        row = self.client.get("/parking-slots").json()[0]

        self.assertIsNone(row["numberplate"])

    def test_logs_include_number_plate(self):
        self.add_slot("a", "SLOT-001", "B1")
        self.client.post("/gate-entry", json={"number_plate": "LOGP1"})

        self.assertEqual(self.client.get("/logs").json()[0]["numberplate"], "LOGP1")

    def test_parking_status_reports_slot_and_cost(self):
        self.add_slot("a", "SLOT-001", "B1")
        self.client.post("/gate-entry", json={"number_plate": "PS1"})
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            started = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
            conn.execute("UPDATE logs SET starttime = ?", (started,))

        response = self.anon().get("/parking-status", params={"number_plate": "ps1"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["slot_name"], "SLOT-001")
        self.assertEqual(body["floor"], "B1")
        self.assertEqual(body["numberplate"], "PS1")
        self.assertEqual(body["minutes_parked"], 25)
        self.assertEqual(body["cost"], 20 + 3 * 3)

    def test_parking_status_errors(self):
        anon = self.anon()

        self.assertEqual(anon.get("/parking-status", params={"number_plate": "NA"}).status_code, 400)
        self.assertEqual(anon.get("/parking-status", params={"number_plate": "NOPE"}).status_code, 404)

    def test_requests_and_gate_entry_are_logged(self):
        self.add_slot("a", "SLOT-001", "B1")

        with self.assertLogs("server", level="INFO") as captured:
            self.client.post("/gate-entry", json={"number_plate": "LOG1"})

        output = "\n".join(captured.output)
        self.assertIn("booked slot a", output)
        self.assertIn("POST /gate-entry -> 200", output)

    def test_failed_requests_are_logged_as_warnings(self):
        with self.assertLogs("server", level="WARNING") as captured:
            self.client.post("/gate-entry", json={"number_plate": "NA"})

        self.assertTrue(any("-> 400" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
