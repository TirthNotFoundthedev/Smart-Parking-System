import sqlite3
import tempfile
import unittest
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

    def test_cors_allows_configured_origin_only(self):
        allowed = self.client.get(
            "/health", headers={"Origin": "http://localhost:3000"}
        )
        blocked = self.client.get(
            "/health", headers={"Origin": "http://evil.example"}
        )

        self.assertEqual(
            allowed.headers.get("access-control-allow-origin"),
            "http://localhost:3000",
        )
        self.assertNotIn("access-control-allow-origin", blocked.headers)

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
