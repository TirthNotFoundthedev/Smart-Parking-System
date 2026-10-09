import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import main


class SlotEditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "parking.db"
        patcher = patch.object(main, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        main.init_db()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.executescript(
                """
                INSERT INTO parkingslots VALUES ('slot-1', 'SLOT-001', 0, 0, 'B1');
                INSERT INTO parkingslots VALUES ('slot-2', 'SLOT-002', 0, 0, 'B1');
                """
            )
        self.client = TestClient(main.app)
        login = self.client.post(
            "/guard/login",
            json={"username": main.GUARD_USERNAME, "password": main.GUARD_PASSWORD},
        )
        self.client.headers["Authorization"] = f"Bearer {login.json()['token']}"

    def edit(self, slot_id="slot-1", **body):
        return self.client.put(f"/parking-slots/{slot_id}", json=body)

    def test_requires_guard_login(self):
        self.client.headers.pop("Authorization")
        self.assertEqual(self.edit(name="X").status_code, 401)

    def test_unknown_slot_is_404(self):
        self.assertEqual(self.edit("nope", name="X").status_code, 404)

    def test_rename_and_move_floor(self):
        body = self.edit(name=" A-1 ", floor="B2").json()
        self.assertEqual((body["name"], body["floor"]), ("A-1", "B2"))

    def test_blank_name_rejected(self):
        self.assertEqual(self.edit(name="  ").status_code, 400)

    def test_assign_plate_books_slot_and_shows_plate(self):
        body = self.edit(number_plate="abc123").json()
        self.assertEqual(body["digitalstatus"], 1)
        self.assertEqual(body["numberplate"], "abc123")
        self.assertEqual(body["booking_status"], "assigned")
        status = self.client.get("/parking-status", params={"number_plate": "ABC123"})
        self.assertEqual(status.json()["slot_name"], "SLOT-001")

    def test_assign_to_occupied_slot_marks_booking_parked(self):
        self.client.put("/sensor/slots/slot-1", json={"occupied": True})
        self.assertEqual(self.edit(number_plate="ABC123").json()["booking_status"], "parked")

    def test_reassign_replaces_holder(self):
        self.edit(number_plate="OLD111")
        body = self.edit(number_plate="NEW222").json()
        self.assertEqual(body["numberplate"], "NEW222")
        old = self.client.get("/parking-status", params={"number_plate": "OLD111"})
        self.assertEqual(old.status_code, 404)

    def test_plate_already_in_another_slot_is_409(self):
        self.edit(number_plate="ABC123")
        self.assertEqual(self.edit("slot-2", number_plate="ABC123").status_code, 409)

    def test_clear_plate_frees_slot_and_closes_log(self):
        self.edit(number_plate="ABC123")
        body = self.edit(number_plate="").json()
        self.assertEqual(body["digitalstatus"], 0)
        self.assertIsNone(body["numberplate"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            open_logs = conn.execute("SELECT COUNT(*) FROM logs WHERE endtime IS NULL").fetchone()[0]
        self.assertEqual(open_logs, 0)

    def test_digital_status_off_releases_holder(self):
        self.edit(number_plate="ABC123")
        self.assertEqual(self.edit(digitalstatus=False).json()["digitalstatus"], 0)

    def test_digital_status_on_needs_a_plate(self):
        self.assertEqual(self.edit(digitalstatus=True).status_code, 400)

    def test_assign_plate_with_free_status_is_contradiction(self):
        self.assertEqual(self.edit(number_plate="ABC123", digitalstatus=False).status_code, 400)


if __name__ == "__main__":
    unittest.main()
