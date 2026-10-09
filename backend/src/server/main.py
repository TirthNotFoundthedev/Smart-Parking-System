import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
import hmac
from math import ceil
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from server import bookings
from server.logging_config import setup_logging

logger = setup_logging()

# How often the no-show sweeper runs. PARKING_EXPIRY_INTERVAL is in seconds.
EXPIRY_INTERVAL_SECONDS = float(os.environ.get("PARKING_EXPIRY_INTERVAL", "30"))


def expire_no_shows() -> int:
    with closing(get_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        expired = bookings.expire_bookings(conn)
    for booking in expired:
        logger.info("No-show: booking %s released slot %s", booking["id"], booking["slotid"])
    return len(expired)


async def _expiry_loop():
    while True:
        await asyncio.sleep(EXPIRY_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(expire_no_shows)
        except Exception:
            logger.exception("No-show sweep failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_expiry_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(lifespan=lifespan)

# Browser origins allowed to call the API: comma-separated CORS_ORIGINS (or the
# older PARKING_CORS_ORIGINS). Defaults to "*" because the API uses bearer
# tokens, not cookies.
CORS_ORIGINS = [
    origin.strip()
    for origin in (
        os.environ.get("CORS_ORIGINS") or os.environ.get("PARKING_CORS_ORIGINS") or "*"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Gateway-Key"],
)

# Fixed demo guard account; tokens live in memory and reset on restart.
GUARD_USERNAME = "user1234"
GUARD_PASSWORD = "pass1234"
guard_tokens: set[str] = set()

# Parking charge: fixed fee plus a rate per started block of minutes.
FIXED_FEE = 20
RATE_PER_BLOCK = 3
BLOCK_MINUTES = 10

DB_PATH = Path(__file__).resolve().parents[2] / "parking.db"


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("%s %s failed with an unhandled error", request.method, request.url.path)
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    level = logger.warning if response.status_code >= 400 else logger.info
    level("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, elapsed_ms)
    return response


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_db()) as conn, conn:
        _create_base_tables(conn)
        bookings.init_schema(conn)


def _create_base_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS parkingslots (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            digitalstatus BOOLEAN NOT NULL,
            physicalstatus BOOLEAN NOT NULL,
            floor TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS logs (
            id TEXT PRIMARY KEY,
            slotid TEXT NOT NULL,
            starttime DATETIME NOT NULL,
            endtime DATETIME,
            userid TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            numberplate TEXT NOT NULL,
            phonenumber TEXT NOT NULL
        );
        """
    )


init_db()


@app.get("/")
def read_root():
    return {"message": "Hello, World!"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/parking-slots")
def parking_slots():
    """All slots. Digitally assigned slots carry the holder's plate and booking status."""
    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT p.*, COALESCE(s.state, 'ok') AS sensor_state,
                   CASE WHEN p.digitalstatus = 1 THEN u.numberplate END AS numberplate,
                   CASE WHEN p.digitalstatus = 1 THEN b.status END AS booking_status
            FROM parkingslots p
            LEFT JOIN slot_sensors s ON s.slotid = p.id
            LEFT JOIN bookings b ON b.id = (
                SELECT id FROM bookings
                WHERE slotid = p.id AND status IN ('assigned', 'parked')
                ORDER BY created_at DESC LIMIT 1
            )
            LEFT JOIN users u ON u.id = b.userid
            """
        ).fetchall()
        return [dict(row) for row in rows]


@app.get("/parking-status")
def parking_status(number_plate: str):
    """Public: the driver's active parking, elapsed minutes and running cost."""
    plate = number_plate.strip()
    if not plate or plate == "NA":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please enter a number plate.",
        )
    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT l.starttime, s.name AS slot_name, s.floor, u.numberplate
            FROM logs l
            JOIN users u ON u.id = l.userid
            JOIN parkingslots s ON s.id = l.slotid
            WHERE u.numberplate = ? COLLATE NOCASE AND l.endtime IS NULL
            ORDER BY l.starttime DESC
            LIMIT 1
            """,
            (plate,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active parking found for this number plate.",
        )
    elapsed = bookings.utcnow() - datetime.fromisoformat(row["starttime"])
    minutes = max(int(elapsed.total_seconds() // 60), 0)
    # Every started block is charged in full.
    cost = FIXED_FEE + RATE_PER_BLOCK * ceil(minutes / BLOCK_MINUTES)
    return {
        "slot_name": row["slot_name"],
        "floor": row["floor"],
        "numberplate": row["numberplate"],
        "starttime": row["starttime"],
        "minutes_parked": minutes,
        "cost": cost,
    }


class SlotOccupancy(BaseModel):
    occupied: bool


@app.put("/sensor/slots/{slot_id}")
def update_slot_sensor(slot_id: str, data: SlotOccupancy):
    """Public simulator/hardware shortcut: set one slot's physical occupancy.

    Goes through the same booking reconciliation as /sensor-events.
    """
    with closing(get_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if bookings.resolve_slot_id(conn, slot_id, None, None) is None:
            conn.rollback()
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found.")
        outcome = bookings.apply_sensor_event(conn, slot_id, data.occupied)
        if outcome == "unbooked":
            logger.warning("Alert: car in slot %s with no booking", slot_id)
        row = conn.execute("SELECT * FROM parkingslots WHERE id = ?", (slot_id,)).fetchone()
        return dict(row)


#! Guard auth

class GuardLogin(BaseModel):
    username: str
    password: str


@app.post("/guard/login")
def guard_login(data: GuardLogin):
    username_ok = secrets.compare_digest(data.username.encode(), GUARD_USERNAME.encode())
    password_ok = secrets.compare_digest(data.password.encode(), GUARD_PASSWORD.encode())
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong username or password.",
        )
    token = secrets.token_urlsafe(32)
    guard_tokens.add(token)
    return {"token": token}


def require_guard(
    authorization: str | None = Header(default=None),
    x_gateway_key: str | None = Header(default=None),
):
    """Guard bearer token, or the gateway key (hardware, plate reader)."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme == "Bearer" and token in guard_tokens:
        return
    expected = os.environ.get("PARKING_GATEWAY_KEY")
    if expected and x_gateway_key is not None and hmac.compare_digest(x_gateway_key, expected):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Guard login required.",
    )


#! Use for All - Functions
def CheckUser(number_plate: str | None = None, phone_number: str | None = None):
    number_plate = number_plate.strip() if number_plate else None
    phone_number = phone_number.strip() if phone_number else None
    number_plate = number_plate if number_plate and number_plate != "NA" else None
    phone_number = phone_number if phone_number and phone_number != "NA" else None

    if number_plate is None and phone_number is None:
        return (400,[])

    conn = get_db()
    try:
        cur = conn.cursor()

        if number_plate and phone_number:
            cur.execute(
                "SELECT * FROM users WHERE numberplate = ? AND phonenumber = ?",
                (number_plate, phone_number),
            )
            row = cur.fetchone()
            if row:
                return (200, dict(row))

            cur.execute("SELECT id FROM users WHERE numberplate = ?", (number_plate,))
            plate_user = cur.fetchone()
            cur.execute("SELECT id FROM users WHERE phonenumber = ?", (phone_number,))
            phone_user = cur.fetchone()
            if plate_user and phone_user and plate_user["id"] != phone_user["id"]:
                return (409, [])
            if plate_user or phone_user:
                cur.execute(
                    "SELECT * FROM users WHERE id = ?",
                    ((plate_user or phone_user)["id"],),
                )
                return (200, dict(cur.fetchone()))
            return (404, [])
        elif number_plate:
            cur.execute(
                "SELECT * FROM users WHERE numberplate = ?",
                (number_plate,),
            )
        else:
            cur.execute(
                "SELECT * FROM users WHERE phonenumber = ?",
                (phone_number,),
            )

        row = cur.fetchone()
        if row:
            return (200, dict(row))
        return (404, [])
    finally:
        conn.close()

def BookParkingSlot(conn: sqlite3.Connection | None = None):
    owns_connection = conn is None
    if conn is None:
        conn = get_db()
    try:
        cur = conn.cursor()
        if owns_connection:
            conn.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT id
            FROM parkingslots
            WHERE digitalstatus = 0 AND physicalstatus = 0
              AND NOT EXISTS (
                  SELECT 1 FROM slot_sensors s
                  WHERE s.slotid = parkingslots.id AND s.state != 'ok'
              )
            ORDER BY floor ASC, name ASC, id ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            if owns_connection:
                conn.rollback()
            return (404, [])

        cur.execute(
            """
            UPDATE parkingslots
            SET digitalstatus = 1
            WHERE id = ? AND digitalstatus = 0 AND physicalstatus = 0
            """,
            (row["id"],),
        )
        if cur.rowcount != 1:
            if owns_connection:
                conn.rollback()
            return (404, [])

        cur.execute("SELECT * FROM parkingslots WHERE id = ?", (row["id"],))
        booked_slot = dict(cur.fetchone())
        if owns_connection:
            conn.commit()
        return (200, booked_slot)
    finally:
        if owns_connection:
            conn.close()

#! Gate Entry System

class GateEntry(BaseModel):
    number_plate: str
    phone_number: str = "NA"
    username: str = "NA"

@app.post("/gate-entry", dependencies=[Depends(require_guard)])
def gate_entry(data: GateEntry):
    status_code, user_data = CheckUser(data.number_plate, data.phone_number)
    warning = None

    if status_code == 400:
        logger.warning("Gate entry rejected: no usable number plate or phone number")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide either number plate or phone number.",
        )
    elif status_code == 409:
        warning = "Conflict: Number plate and phone number belong to different users. Prioritized number plate."
        logger.warning("Gate entry identity conflict for plate %r: using plate-matched user", data.number_plate)
        _, user_data = CheckUser(data.number_plate, None)

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if status_code == 404:
            new_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users (id, name, numberplate, phonenumber) VALUES (?, ?, ?, ?)",
                (new_id, data.username, data.number_plate, data.phone_number),
            )
            user_data = {
                "id": new_id,
                "name": data.username,
                "numberplate": data.number_plate,
                "phonenumber": data.phone_number,
            }

        active = conn.execute(
            "SELECT slotid FROM logs WHERE userid = ? AND endtime IS NULL",
            (user_data["id"],),
        ).fetchone()
        if active:
            conn.rollback()
            logger.warning("Gate entry blocked: user %s already has an active booking", user_data["id"])
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already has an active parking booking.",
            )

        slot_status, parking_slot = BookParkingSlot(conn)
        if slot_status == 404:
            conn.rollback()
            logger.warning("Gate entry failed: no available parking slots (user %s rolled back)", user_data["id"])
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No available parking slots.",
            )
        entered_at = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO logs (id, slotid, starttime, userid) VALUES (?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                parking_slot["id"],
                entered_at.isoformat(),
                user_data["id"],
            ),
        )
        bookings.create_walkin_booking(conn, user_data["id"], parking_slot["id"], entered_at)
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Gate entry failed with an unexpected error")
        raise
    finally:
        conn.close()

    logger.info(
        "Gate entry: user %s booked slot %s (%s, floor %s)",
        user_data["id"],
        parking_slot["id"],
        parking_slot["name"],
        parking_slot["floor"],
    )
    return {
        "userdata": user_data,
        "parking_slot": parking_slot,
        "warning": warning,
    }


#! Gate Exit System

class GateExit(BaseModel):
    number_plate: str
    phone_number: str = "NA"

@app.post("/gate-exit", dependencies=[Depends(require_guard)])
def gate_exit(data: GateExit):
    status_code, user_data = CheckUser(data.number_plate, data.phone_number)
    warning = None

    if status_code == 400:
        logger.warning("Gate exit rejected: no usable number plate or phone number")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide either number plate or phone number.",
        )
    elif status_code == 404:
        logger.warning("Gate exit failed: no user matches plate %r / phone %r", data.number_plate, data.phone_number)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    elif status_code == 409:
        warning = "Conflict: Number plate and phone number belong to different users. Prioritized number plate."
        logger.warning("Gate exit identity conflict for plate %r: using plate-matched user", data.number_plate)
        _, user_data = CheckUser(data.number_plate, None)

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT id, slotid, starttime FROM logs WHERE userid = ? AND endtime IS NULL",
            (user_data["id"],),
        ).fetchone()
        if active is None:
            conn.rollback()
            logger.warning("Gate exit failed: user %s has no active booking", user_data["id"])
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User has no active parking booking.",
            )

        endtime = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE logs SET endtime = ? WHERE id = ?", (endtime, active["id"]))
        conn.execute("UPDATE parkingslots SET digitalstatus = 0 WHERE id = ?", (active["slotid"],))
        bookings.complete_active_booking(conn, user_data["id"], active["slotid"])
        slot = conn.execute(
            "SELECT * FROM parkingslots WHERE id = ?", (active["slotid"],)
        ).fetchone()
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Gate exit failed with an unexpected error")
        raise
    finally:
        conn.close()

    logger.info("Gate exit: user %s released slot %s", user_data["id"], active["slotid"])
    return {
        "userdata": user_data,
        "parking_slot": dict(slot) if slot else None,
        "log": {
            "id": active["id"],
            "slotid": active["slotid"],
            "starttime": active["starttime"],
            "endtime": endtime,
            "userid": user_data["id"],
        },
        "warning": warning,
    }


#! Guard slot editing

class SlotUpdate(BaseModel):
    name: str | None = None
    floor: str | None = None
    digitalstatus: bool | None = None
    # None leaves the holder alone; "" or "NA" releases the slot.
    number_plate: str | None = None


@app.put("/parking-slots/{slot_id}", dependencies=[Depends(require_guard)])
def update_parking_slot(slot_id: str, data: SlotUpdate):
    """Guard edit of one slot: name, floor, digital status and assigned plate."""
    plate = data.number_plate.strip() if data.number_plate is not None else None
    release = plate is not None and plate.upper() in ("", "NA")
    assign = plate is not None and not release
    if data.name is not None and not data.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slot name cannot be empty.")
    if data.floor is not None and not data.floor.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Floor cannot be empty.")
    if assign and data.digitalstatus is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign a number plate and set the slot to free.",
        )
    release = release or (data.digitalstatus is False and not assign)

    with closing(get_db()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            slot = conn.execute("SELECT * FROM parkingslots WHERE id = ?", (slot_id,)).fetchone()
            if slot is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found.")
            holder = conn.execute(
                "SELECT * FROM bookings WHERE slotid = ? AND status IN ('assigned', 'parked') LIMIT 1",
                (slot_id,),
            ).fetchone()
            if data.digitalstatus is True and not assign and holder is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Enter a number plate to mark a free slot as assigned.",
                )

            now = datetime.now(timezone.utc)
            if release and holder is not None:
                _end_holding(conn, holder, "cancelled", now)
            elif assign:
                user = _user_for_plate(conn, plate)
                if holder is None or holder["userid"] != user["id"]:
                    elsewhere = conn.execute(
                        """
                        SELECT p.name FROM bookings b JOIN parkingslots p ON p.id = b.slotid
                        WHERE b.userid = ? AND b.status IN ('assigned', 'parked')
                        """,
                        (user["id"],),
                    ).fetchone()
                    if elsewhere:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"{user['numberplate']} is already assigned to {elsewhere['name']}.",
                        )
                    if holder is not None:
                        _end_holding(conn, holder, "cancelled", now)
                    conn.execute(
                        "INSERT INTO logs (id, slotid, starttime, userid) VALUES (?, ?, ?, ?)",
                        (str(uuid.uuid4()), slot_id, now.isoformat(), user["id"]),
                    )
                    booking_id = bookings.create_walkin_booking(conn, user["id"], slot_id, now)
                    if slot["physicalstatus"]:
                        conn.execute("UPDATE bookings SET status = 'parked' WHERE id = ?", (booking_id,))
                    conn.execute("UPDATE parkingslots SET digitalstatus = 1 WHERE id = ?", (slot_id,))

            if data.name is not None:
                conn.execute("UPDATE parkingslots SET name = ? WHERE id = ?", (data.name.strip(), slot_id))
            if data.floor is not None:
                conn.execute("UPDATE parkingslots SET floor = ? WHERE id = ?", (data.floor.strip(), slot_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    logger.info("Guard edited slot %s", slot_id)
    return next(s for s in parking_slots() if s["id"] == slot_id)


def _end_holding(conn, booking, outcome: str, now: datetime) -> None:
    conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (outcome, booking["id"]))
    conn.execute(
        "UPDATE logs SET endtime = ? WHERE userid = ? AND slotid = ? AND endtime IS NULL",
        (now.isoformat(), booking["userid"], booking["slotid"]),
    )
    conn.execute("UPDATE parkingslots SET digitalstatus = 0 WHERE id = ?", (booking["slotid"],))


def _user_for_plate(conn, plate: str):
    row = conn.execute("SELECT * FROM users WHERE numberplate = ? COLLATE NOCASE", (plate,)).fetchone()
    if row:
        return row
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, name, numberplate, phonenumber) VALUES (?, 'NA', ?, 'NA')",
        (user_id, plate),
    )
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


#! Log history

@app.get("/logs", dependencies=[Depends(require_guard)])
def list_logs(user_id: str | None = None, active: bool | None = None, limit: int = 100):
    """Entry/exit history, newest first. `active=true` lists open entries only."""
    query = "SELECT l.*, u.numberplate FROM logs l LEFT JOIN users u ON u.id = l.userid"
    clauses, params = [], []
    if user_id is not None:
        clauses.append("l.userid = ?")
        params.append(user_id)
    if active is not None:
        clauses.append("l.endtime IS NULL" if active else "l.endtime IS NOT NULL")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY l.starttime DESC LIMIT ?"
    params.append(max(1, min(limit, 1000)))

    with closing(get_db()) as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]

#! Gateway (sensor ingest)

def require_gateway_key(x_gateway_key: str | None):
    expected = os.environ.get("PARKING_GATEWAY_KEY")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway access is not configured (set PARKING_GATEWAY_KEY).",
        )
    if x_gateway_key is None or not hmac.compare_digest(x_gateway_key, expected):
        logger.warning("Gateway request rejected: bad or missing X-Gateway-Key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid gateway key.",
        )


class SlotMapEntry(BaseModel):
    slot_id: str
    node: int
    channel: int


@app.post("/gateway/slot-map")
def set_slot_map(entries: list[SlotMapEntry], x_gateway_key: str | None = Header(default=None)):
    """Map each slot to the Arduino node and sensor channel that watches it."""
    require_gateway_key(x_gateway_key)
    with closing(get_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        for entry in entries:
            if conn.execute("SELECT 1 FROM parkingslots WHERE id = ?", (entry.slot_id,)).fetchone() is None:
                conn.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Unknown slot {entry.slot_id!r}.",
                )
            try:
                conn.execute(
                    """
                    INSERT INTO slot_sensors (slotid, node_id, channel) VALUES (?, ?, ?)
                    ON CONFLICT(slotid) DO UPDATE SET node_id = excluded.node_id, channel = excluded.channel
                    """,
                    (entry.slot_id, entry.node, entry.channel),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Node {entry.node} channel {entry.channel} is already mapped to another slot.",
                )
    return {"mapped": len(entries)}


class SensorEvent(BaseModel):
    """A slot reading, addressed by slot_id or by node + channel."""
    slot_id: str | None = None
    node: int | None = None
    channel: int | None = None
    occupied: bool


@app.post("/sensor-events")
def sensor_events(events: list[SensorEvent], x_gateway_key: str | None = Header(default=None)):
    """Gateway pushes slot state changes. Unknown slots are reported, not fatal."""
    require_gateway_key(x_gateway_key)
    results, rejected = [], []
    with closing(get_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        for index, event in enumerate(events):
            slot_id = bookings.resolve_slot_id(conn, event.slot_id, event.node, event.channel)
            if slot_id is None:
                rejected.append({"index": index, "reason": "Unknown slot or unmapped node/channel."})
                continue
            outcome = bookings.apply_sensor_event(conn, slot_id, event.occupied)
            results.append({"index": index, "slot_id": slot_id, "outcome": outcome})
            if outcome == "unbooked":
                logger.warning("Alert: car in slot %s with no booking", slot_id)
    return {"applied": results, "rejected": rejected}


class NodeStatus(BaseModel):
    node: int
    online: bool


class Heartbeat(BaseModel):
    nodes: list[NodeStatus]


@app.post("/gateway/heartbeat")
def gateway_heartbeat(data: Heartbeat, x_gateway_key: str | None = Header(default=None)):
    """Report which nodes answered their last poll. Offline nodes make their slots unassignable."""
    require_gateway_key(x_gateway_key)
    with closing(get_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        for node in data.nodes:
            bookings.set_node_online(conn, node.node, node.online)
            if not node.online:
                logger.warning("Node %s reported offline", node.node)
    return {"nodes": len(data.nodes)}


#! Drivers

MY_SLOT_LIMIT_PER_MINUTE = 10
_my_slot_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(client: str) -> bool:
    now = time.monotonic()
    hits = _my_slot_hits[client]
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= MY_SLOT_LIMIT_PER_MINUTE:
        return True
    hits.append(now)
    return False


@app.get("/my-slot")
def my_slot(plate: str, request: Request):
    """Where did I park? Looks up the active booking by number plate."""
    client = request.client.host if request.client else "unknown"
    if _rate_limited(client):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many lookups. Try again in a minute.",
        )
    plate = plate.strip()
    status_code, user = CheckUser(plate, None)
    if status_code != 200:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active booking for this plate.")
    with closing(get_db()) as conn:
        booking = bookings.active_booking_for_user(conn, user["id"])
        if booking is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active booking for this plate.")
        slot = dict(conn.execute("SELECT * FROM parkingslots WHERE id = ?", (booking["slotid"],)).fetchone())
    return {
        "parking_slot": slot,
        "booking_status": booking["status"],
        "expires_at": booking["expires_at"] if booking["status"] == "assigned" else None,
        "directions": bookings.directions(slot),
    }


@app.get("/availability")
def availability():
    """Free and total slot counts per floor. Offline-sensor slots count as not free."""
    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT p.floor AS floor, COUNT(*) AS total,
                   SUM(CASE WHEN p.digitalstatus = 0 AND p.physicalstatus = 0
                             AND COALESCE(s.state, 'ok') = 'ok' THEN 1 ELSE 0 END) AS free
            FROM parkingslots p LEFT JOIN slot_sensors s ON s.slotid = p.id
            GROUP BY p.floor ORDER BY p.floor
            """
        ).fetchall()
    return [dict(row) for row in rows]


#! Guard alerts

@app.get("/alerts", dependencies=[Depends(require_guard)])
def list_alerts(open: bool = True):
    """Guard alerts, newest first. `open=false` lists resolved ones."""
    clause = "resolved_at IS NULL" if open else "resolved_at IS NOT NULL"
    with closing(get_db()) as conn:
        rows = conn.execute(f"SELECT * FROM alerts WHERE {clause} ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


@app.post("/alerts/{alert_id}/resolve", dependencies=[Depends(require_guard)])
def resolve_alert(alert_id: str):
    with closing(get_db()) as conn, conn:
        cur = conn.execute(
            "UPDATE alerts SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            (bookings.utcnow().isoformat(), alert_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No open alert with that id.")
    return {"resolved": alert_id}


#! Booking list (guard view)

@app.get("/bookings", dependencies=[Depends(require_guard)])
def list_bookings(active: bool | None = None, limit: int = 100):
    """Bookings with the driver and slot attached, newest first.

    `active=true` lists cars that are assigned or parked; `active=false` lists
    finished ones (completed, no-show, cancelled).
    """
    query = """
        SELECT b.id, b.kind, b.status, b.start_time, b.expires_at,
               u.id AS userid, u.name AS username, u.numberplate, u.phonenumber,
               p.id AS slotid, p.name AS slotname, p.floor
        FROM bookings b
        JOIN users u ON u.id = b.userid
        JOIN parkingslots p ON p.id = b.slotid
    """
    if active is True:
        query += " WHERE b.status IN ('assigned', 'parked')"
    elif active is False:
        query += " WHERE b.status NOT IN ('assigned', 'parked')"
    query += " ORDER BY b.start_time DESC LIMIT ?"
    with closing(get_db()) as conn:
        rows = conn.execute(query, (max(1, min(limit, 1000)),)).fetchall()
    return [dict(row) for row in rows]
