import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import main


class GateExitTests(unittest.TestCase):
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
                INSERT INTO parkingslots VALUES ('slot-1', 'SLOT-001', 0, 0, 'B1');
                """
            )
        main.init_db()
        self.client = TestClient(main.app)

    def query(self, sql, params=()):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def enter(self, plate="ABC123", **extra):
        response = self.client.post(
            "/gate-entry", json={"number_plate": plate, **extra}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_exit_closes_the_log_row_and_frees_the_slot(self):
        entry = self.enter()

        response = self.client.post("/gate-exit", json={"number_plate": "ABC123"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["userdata"]["id"], entry["userdata"]["id"])
        self.assertEqual(body["parking_slot"]["digitalstatus"], 0)
        self.assertIsNotNone(body["log"]["endtime"])
        logs = self.query("SELECT * FROM logs")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["endtime"], body["log"]["endtime"])
        self.assertEqual(self.query("SELECT digitalstatus FROM parkingslots")[0]["digitalstatus"], 0)

    def test_exit_can_identify_user_by_phone_only(self):
        self.enter(phone_number="5551000")

        response = self.client.post(
            "/gate-exit", json={"number_plate": "NA", "phone_number": "5551000"}
        )

        self.assertEqual(response.status_code, 200)

    def test_user_can_reenter_after_exit_and_gets_a_new_log_row(self):
        self.enter()
        self.client.post("/gate-exit", json={"number_plate": "ABC123"})

        again = self.client.post("/gate-entry", json={"number_plate": "ABC123"})

        self.assertEqual(again.status_code, 200)
        logs = self.query("SELECT * FROM logs ORDER BY starttime")
        self.assertEqual(len(logs), 2)
        self.assertIsNotNone(logs[0]["endtime"])
        self.assertIsNone(logs[1]["endtime"])

    def test_exit_without_active_booking_is_not_found(self):
        self.enter()
        self.client.post("/gate-exit", json={"number_plate": "ABC123"})

        response = self.client.post("/gate-exit", json={"number_plate": "ABC123"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "User has no active parking booking.")

    def test_exit_for_unknown_user_is_not_found(self):
        response = self.client.post("/gate-exit", json={"number_plate": "NOPE1"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "User not found.")

    def test_exit_without_identifiers_is_bad_request(self):
        response = self.client.post("/gate-exit", json={"number_plate": "NA"})

        self.assertEqual(response.status_code, 400)

    def test_exit_requires_number_plate_field(self):
        response = self.client.post("/gate-exit", json={"phone_number": "5551000"})

        self.assertEqual(response.status_code, 422)

    def test_exit_only_releases_the_exiting_users_slot(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("INSERT INTO parkingslots VALUES ('slot-2', 'SLOT-002', 0, 0, 'B1')")
        self.enter("FIRST1")
        self.enter("SECOND1")

        self.client.post("/gate-exit", json={"number_plate": "FIRST1"})

        slots = {r["id"]: r["digitalstatus"] for r in self.query("SELECT * FROM parkingslots")}
        self.assertEqual(slots, {"slot-1": 0, "slot-2": 1})

    def test_logs_endpoint_lists_entries_and_exits_with_filters(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("INSERT INTO parkingslots VALUES ('slot-2', 'SLOT-002', 0, 0, 'B1')")
        first = self.enter("FIRST1")
        self.enter("SECOND1")
        self.client.post("/gate-exit", json={"number_plate": "FIRST1"})

        everything = self.client.get("/logs").json()
        open_only = self.client.get("/logs", params={"active": "true"}).json()
        closed_only = self.client.get("/logs", params={"active": "false"}).json()
        by_user = self.client.get(
            "/logs", params={"user_id": first["userdata"]["id"]}
        ).json()

        self.assertEqual(len(everything), 2)
        self.assertEqual(len(open_only), 1)
        self.assertEqual(len(closed_only), 1)
        self.assertEqual(len(by_user), 1)
        self.assertEqual(by_user[0]["userid"], first["userdata"]["id"])

    def test_exit_is_logged(self):
        self.enter()

        with self.assertLogs("server", level="INFO") as captured:
            self.client.post("/gate-exit", json={"number_plate": "ABC123"})

        self.assertIn("released slot slot-1", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
