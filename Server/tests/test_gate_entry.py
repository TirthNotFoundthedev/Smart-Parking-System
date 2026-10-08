import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import main


class GateEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.db_path = Path(self.temp_dir.name) / "parking.db"
        self.db_path_patch = patch.object(main, "DB_PATH", self.db_path)
        self.db_path_patch.start()
        self.addCleanup(self.db_path_patch.stop)

        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE parkingslots (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    digitalstatus BOOLEAN NOT NULL,
                    physicalstatus BOOLEAN NOT NULL,
                    floor TEXT NOT NULL
                );
                CREATE TABLE logs (
                    id TEXT PRIMARY KEY,
                    slotid TEXT NOT NULL,
                    starttime DATETIME NOT NULL,
                    endtime DATETIME,
                    userid TEXT NOT NULL
                );
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    numberplate TEXT NOT NULL,
                    phonenumber TEXT NOT NULL
                );
                """
            )

        self.client = TestClient(main.app)

    def add_user(
        self,
        user_id="user-1",
        name="Existing User",
        number_plate="ABC123",
        phone_number="5551000",
    ):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO users (id, name, numberplate, phonenumber)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, name, number_plate, phone_number),
            )

    def add_slot(
        self,
        slot_id="slot-1",
        name="SLOT-001",
        digital_status=0,
        physical_status=0,
        floor="B1",
    ):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO parkingslots
                    (id, name, digitalstatus, physicalstatus, floor)
                VALUES (?, ?, ?, ?, ?)
                """,
                (slot_id, name, digital_status, physical_status, floor),
            )

    def get_user(self, user_id):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def test_gate_entry_dashboard_renders_ui_that_posts_to_gate_entry(self):
        response = self.client.get("/dashboard/gate-entry")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn('fetch("/gate-entry"', response.text)
        self.assertIn("Draft without direction", response.text)

    def get_slot(self, slot_id):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM parkingslots WHERE id = ?", (slot_id,)
            ).fetchone()
        return dict(row) if row else None

    def user_count(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def test_existing_user_matching_both_identifiers_can_enter(self):
        self.add_user()
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "ABC123", "phone_number": "5551000"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["userdata"]["id"], "user-1")
        self.assertEqual(body["parking_slot"]["id"], "slot-1")
        self.assertIsNone(body["warning"])
        self.assertEqual(self.user_count(), 1)
        self.assertEqual(self.get_slot("slot-1")["digitalstatus"], 1)

    def test_existing_user_can_be_found_by_plate_only(self):
        self.add_user()
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "ABC123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["userdata"]["id"], "user-1")
        self.assertEqual(self.user_count(), 1)

    def test_existing_user_can_be_found_by_phone_with_na_plate(self):
        self.add_user(number_plate="ABC123", phone_number="5551000")
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "NA", "phone_number": "5551000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["userdata"]["id"], "user-1")

    def test_existing_user_is_used_when_only_plate_matches(self):
        self.add_user()
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "ABC123", "phone_number": "5559999"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["userdata"]["id"], "user-1")
        self.assertEqual(self.get_user("user-1")["phonenumber"], "5551000")
        self.assertIsNone(response.json()["warning"])

    def test_existing_user_is_used_when_only_phone_matches(self):
        self.add_user()
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "XYZ999", "phone_number": "5551000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["userdata"]["id"], "user-1")
        self.assertEqual(self.get_user("user-1")["numberplate"], "ABC123")

    def test_plate_and_phone_conflict_prioritizes_plate_user(self):
        self.add_user(
            user_id="plate-user",
            name="Plate User",
            number_plate="ABC123",
            phone_number="5551000",
        )
        self.add_user(
            user_id="phone-user",
            name="Phone User",
            number_plate="XYZ999",
            phone_number="5552000",
        )
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "ABC123", "phone_number": "5552000"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["userdata"]["id"], "plate-user")
        self.assertEqual(
            body["warning"],
            "Conflict: Number plate and phone number belong to different users. "
            "Prioritized number plate.",
        )

    def test_unknown_identifiers_create_user_and_book_slot(self):
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={
                "number_plate": "NEW123",
                "phone_number": "5553000",
                "username": "New User",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["userdata"]["name"], "New User")
        self.assertEqual(body["userdata"]["numberplate"], "NEW123")
        self.assertEqual(body["userdata"]["phonenumber"], "5553000")
        self.assertIsNotNone(self.get_user(body["userdata"]["id"]))
        self.assertIsNone(body["warning"])

    def test_new_user_name_defaults_to_na(self):
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "NEW123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["userdata"]["name"], "NA")
        self.assertEqual(response.json()["userdata"]["phonenumber"], "NA")

    def test_no_identifiers_returns_bad_request(self):
        for payload in (
            {"number_plate": "NA"},
            {"number_plate": "", "phone_number": "NA"},
            {"number_plate": "   ", "phone_number": "   "},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/gate-entry", json=payload)
                self.assertEqual(response.status_code, 400)

    def test_missing_number_plate_is_rejected_by_request_validation(self):
        response = self.client.post(
            "/gate-entry",
            json={"phone_number": "5551000"},
        )

        self.assertEqual(response.status_code, 422)

    def test_null_number_plate_is_rejected_by_request_validation(self):
        response = self.client.post(
            "/gate-entry",
            json={"number_plate": None, "phone_number": "5551000"},
        )

        self.assertEqual(response.status_code, 422)

    def test_wrong_field_types_are_rejected(self):
        invalid_payloads = (
            {"number_plate": 123},
            {"number_plate": "ABC123", "phone_number": 5551000},
            {"number_plate": "ABC123", "username": ["not", "a", "string"]},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/gate-entry", json=payload)
                self.assertEqual(response.status_code, 422)

    def test_malformed_json_is_rejected(self):
        response = self.client.post(
            "/gate-entry",
            content="{not valid json",
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 422)

    def test_full_lot_returns_not_found_for_existing_user(self):
        self.add_user()
        self.add_slot(digital_status=1, physical_status=0)
        self.add_slot(slot_id="slot-2", digital_status=0, physical_status=1)

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "ABC123", "phone_number": "5551000"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "No available parking slots.")
        self.assertEqual(self.user_count(), 1)

    def test_full_lot_does_not_leave_a_new_user_behind(self):
        self.add_slot(digital_status=1, physical_status=0)

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "NEW123", "phone_number": "5553000"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.user_count(), 0)

    def test_slot_is_not_booked_if_either_status_marks_it_unavailable(self):
        self.add_slot(digital_status=1, physical_status=0)
        self.add_slot(
            slot_id="slot-2",
            name="SLOT-002",
            digital_status=0,
            physical_status=1,
        )

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "NEW123"},
        )

        self.assertEqual(response.status_code, 404)

    def test_multiple_entries_book_different_available_slots(self):
        self.add_slot()
        self.add_slot(slot_id="slot-2", name="SLOT-002")

        first = self.client.post(
            "/gate-entry", json={"number_plate": "FIRST123"}
        )
        second = self.client.post(
            "/gate-entry", json={"number_plate": "SECOND123"}
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(
            first.json()["parking_slot"]["id"],
            second.json()["parking_slot"]["id"],
        )
        self.assertEqual(self.get_slot("slot-1")["digitalstatus"], 1)
        self.assertEqual(self.get_slot("slot-2")["digitalstatus"], 1)

    def test_response_slot_status_reflects_the_committed_booking(self):
        self.add_slot()

        response = self.client.post(
            "/gate-entry",
            json={"number_plate": "NEW123"},
        )

        self.assertEqual(response.status_code, 200)
        slot_id = response.json()["parking_slot"]["id"]
        self.assertEqual(
            response.json()["parking_slot"]["digitalstatus"],
            self.get_slot(slot_id)["digitalstatus"],
        )
        self.assertEqual(response.json()["parking_slot"]["digitalstatus"], 1)

    def log_rows(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM logs")]

    def test_successful_entry_writes_an_open_log_row(self):
        self.add_slot()

        response = self.client.post("/gate-entry", json={"number_plate": "LOG123"})

        self.assertEqual(response.status_code, 200)
        logs = self.log_rows()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["slotid"], "slot-1")
        self.assertEqual(logs[0]["userid"], response.json()["userdata"]["id"])
        self.assertIsNone(logs[0]["endtime"])

    def test_user_with_active_booking_cannot_book_a_second_slot(self):
        self.add_slot()
        self.add_slot(slot_id="slot-2", name="SLOT-002")

        first = self.client.post("/gate-entry", json={"number_plate": "DUP123"})
        second = self.client.post("/gate-entry", json={"number_plate": "DUP123"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.get_slot("slot-2")["digitalstatus"], 0)
        self.assertEqual(len(self.log_rows()), 1)


if __name__ == "__main__":
    unittest.main()
