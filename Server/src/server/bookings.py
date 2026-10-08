"""Booking lifecycle, sensor ingest and alerts.

Every function takes an open sqlite3 connection and never commits; the caller
owns the transaction. Timestamps are timezone-aware UTC datetimes and are
stored as ISO 8601 strings.
"""

from datetime import datetime, timedelta, timezone
import sqlite3
import uuid

NOSHOW_MINUTES = 15
ACTIVE_STATUSES = ("assigned", "parked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    userid TEXT NOT NULL,
    slotid TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('walkin', 'reservation')),
    status TEXT NOT NULL CHECK (status IN
        ('reserved', 'assigned', 'parked', 'completed', 'noshow', 'cancelled')),
    start_time DATETIME NOT NULL,
    expires_at DATETIME,
    created_at DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    slotid TEXT,
    type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL,
    resolved_at DATETIME
);
CREATE TABLE IF NOT EXISTS slot_sensors (
    slotid TEXT PRIMARY KEY,
    node_id INTEGER,
    channel INTEGER,
    state TEXT NOT NULL DEFAULT 'ok' CHECK (state IN ('ok', 'unknown', 'fault')),
    last_seen DATETIME,
    UNIQUE (node_id, channel)
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# --- bookings ---------------------------------------------------------------

def create_walkin_booking(conn, user_id: str, slot_id: str, now: datetime | None = None) -> str:
    now = now or utcnow()
    booking_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO bookings (id, userid, slotid, kind, status, start_time, expires_at, created_at)
        VALUES (?, ?, ?, 'walkin', 'assigned', ?, ?, ?)
        """,
        (
            booking_id,
            user_id,
            slot_id,
            now.isoformat(),
            (now + timedelta(minutes=NOSHOW_MINUTES)).isoformat(),
            now.isoformat(),
        ),
    )
    return booking_id


def complete_active_booking(conn, user_id: str, slot_id: str) -> None:
    conn.execute(
        "UPDATE bookings SET status = 'completed' WHERE userid = ? AND slotid = ? AND status IN ('assigned', 'parked')",
        (user_id, slot_id),
    )


def expire_bookings(conn, now: datetime | None = None) -> list[dict]:
    """Release assigned bookings that were never parked within the window.

    Returns the expired bookings. Only reservations count as strikes (see
    ARCHITECTURE.md section 10); that is derived from `kind` later, so a
    walk-in no-show just frees the slot.
    """
    now = now or utcnow()
    expired = []
    rows = conn.execute(
        "SELECT * FROM bookings WHERE status = 'assigned' AND expires_at IS NOT NULL"
    ).fetchall()
    for row in rows:
        if datetime.fromisoformat(row["expires_at"]) > now:
            continue
        conn.execute("UPDATE bookings SET status = 'noshow' WHERE id = ?", (row["id"],))
        conn.execute("UPDATE parkingslots SET digitalstatus = 0 WHERE id = ?", (row["slotid"],))
        conn.execute(
            "UPDATE logs SET endtime = ? WHERE userid = ? AND slotid = ? AND endtime IS NULL",
            (now.isoformat(), row["userid"], row["slotid"]),
        )
        expired.append(dict(row))
    return expired


def active_booking_for_user(conn, user_id: str):
    return conn.execute(
        "SELECT * FROM bookings WHERE userid = ? AND status IN ('assigned', 'parked') ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def directions(slot: sqlite3.Row | dict) -> str:
    # Placeholder until the lot has a real map: floor and slot name only.
    return f"Go to floor {slot['floor']} and look for {slot['name']}."


# --- alerts -----------------------------------------------------------------

def open_alert(conn, alert_type: str, slot_id: str | None, detail: str = "", now: datetime | None = None) -> None:
    exists = conn.execute(
        "SELECT 1 FROM alerts WHERE type = ? AND slotid IS ? AND detail = ? AND resolved_at IS NULL",
        (alert_type, slot_id, detail),
    ).fetchone()
    if exists:
        return
    conn.execute(
        "INSERT INTO alerts (id, slotid, type, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), slot_id, alert_type, detail, (now or utcnow()).isoformat()),
    )


def resolve_alerts(conn, alert_type: str, slot_id: str | None, detail: str | None = None, now: datetime | None = None) -> None:
    sql = "UPDATE alerts SET resolved_at = ? WHERE type = ? AND slotid IS ? AND resolved_at IS NULL"
    params: list = [(now or utcnow()).isoformat(), alert_type, slot_id]
    if detail is not None:
        sql += " AND detail = ?"
        params.append(detail)
    conn.execute(sql, params)


# --- sensors ----------------------------------------------------------------

def resolve_slot_id(conn, slot_id: str | None, node: int | None, channel: int | None) -> str | None:
    if slot_id is not None:
        row = conn.execute("SELECT id FROM parkingslots WHERE id = ?", (slot_id,)).fetchone()
        return row["id"] if row else None
    if node is None or channel is None:
        return None
    row = conn.execute(
        "SELECT slotid FROM slot_sensors WHERE node_id = ? AND channel = ?", (node, channel)
    ).fetchone()
    return row["slotid"] if row else None


def apply_sensor_event(conn, slot_id: str, occupied: bool, now: datetime | None = None) -> str:
    """Record a physical reading and reconcile it with the booking.

    Returns what happened: 'parked' (assigned car arrived), 'unbooked' (car
    with no booking, guard alerted), 'occupied', or 'vacated'.
    """
    now = now or utcnow()
    conn.execute("UPDATE parkingslots SET physicalstatus = ? WHERE id = ?", (int(occupied), slot_id))
    conn.execute(
        """
        INSERT INTO slot_sensors (slotid, state, last_seen) VALUES (?, 'ok', ?)
        ON CONFLICT(slotid) DO UPDATE SET last_seen = excluded.last_seen,
            state = CASE WHEN state = 'unknown' THEN 'ok' ELSE state END
        """,
        (slot_id, now.isoformat()),
    )

    if not occupied:
        resolve_alerts(conn, "unbooked_car", slot_id, now=now)
        return "vacated"

    booking = conn.execute(
        "SELECT id, status FROM bookings WHERE slotid = ? AND status IN ('assigned', 'parked') LIMIT 1",
        (slot_id,),
    ).fetchone()
    if booking is None:
        open_alert(conn, "unbooked_car", slot_id, "Car detected in a slot with no booking.", now)
        return "unbooked"
    if booking["status"] == "assigned":
        conn.execute("UPDATE bookings SET status = 'parked' WHERE id = ?", (booking["id"],))
        return "parked"
    return "occupied"


def set_node_online(conn, node_id: int, online: bool, now: datetime | None = None) -> int:
    """Mark every slot on a node reachable or unknown. Returns slots touched."""
    now = now or utcnow()
    rows = conn.execute("SELECT slotid FROM slot_sensors WHERE node_id = ?", (node_id,)).fetchall()
    detail = f"Node {node_id} is not responding."
    if online:
        conn.execute(
            "UPDATE slot_sensors SET state = 'ok', last_seen = ? WHERE node_id = ? AND state = 'unknown'",
            (now.isoformat(), node_id),
        )
        resolve_alerts(conn, "node_offline", None, detail, now)
    else:
        conn.execute("UPDATE slot_sensors SET state = 'unknown' WHERE node_id = ?", (node_id,))
        open_alert(conn, "node_offline", None, detail, now)
    return len(rows)
