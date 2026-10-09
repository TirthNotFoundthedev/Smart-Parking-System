from datetime import datetime, timezone
from math import ceil
from pathlib import Path
import os
import secrets
import sqlite3
import uuid
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

DB_PATH = Path(__file__).resolve().parents[2] / "parking.db"

FIXED_FEE = 20
RATE_PER_BLOCK = 3
BLOCK_MINUTES = 10

GUARD_USERNAME = "user1234"
GUARD_PASSWORD = "pass1234"
guard_tokens: set[str] = set()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


with get_db() as conn:
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


@app.get("/")
def read_root():
    return {"message": "Hello, World!"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/parking-slots")
def parking_slots():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM parkingslots")
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
        
        
@app.get("/parking-status")
def parking_status(number_plate: str):
    plate = number_plate.strip()
    if not plate or plate == "NA":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please enter a number plate.",
        )

    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT l.starttime, s.name AS slot_name, s.floor
            FROM logs l
            JOIN users u ON u.id = l.userid
            JOIN parkingslots s ON s.id = l.slotid
            WHERE u.numberplate = ? COLLATE NOCASE AND l.endtime IS NULL
            ORDER BY l.starttime DESC
            LIMIT 1
            """,
            (plate,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active parking found for this number plate.",
        )

    elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(row["starttime"])
    minutes = max(int(elapsed.total_seconds() // 60), 0)
    # Every started block is charged in full.
    cost = FIXED_FEE + RATE_PER_BLOCK * ceil(minutes / BLOCK_MINUTES)
    return {
        "slot_name": row["slot_name"],
        "floor": row["floor"],
        "starttime": row["starttime"],
        "minutes_parked": minutes,
        "cost": cost,
    }


class SlotOccupancy(BaseModel):
    occupied: bool


@app.put("/sensor/slots/{slot_id}")
def update_slot_sensor(slot_id: str, data: SlotOccupancy):
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE parkingslots SET physicalstatus = ? WHERE id = ?",
            (int(data.occupied), slot_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Slot not found.",
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM parkingslots WHERE id = ?", (slot_id,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


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
            ORDER BY id ASC
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


def require_guard(authorization: str | None = Header(default=None)):
    scheme, _, token = (authorization or "").partition(" ")
    if scheme != "Bearer" or token not in guard_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Guard login required.",
        )


class GateEntry(BaseModel):
    number_plate: str
    phone_number: str = "NA"
    username: str = "NA"

@app.post("/gate-entry", dependencies=[Depends(require_guard)])
def gate_entry(data: GateEntry):
    status_code, user_data = CheckUser(data.number_plate, data.phone_number)
    warning = None

    if status_code == 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide either number plate or phone number.",
        )
    elif status_code == 409:
        warning = "Conflict: Number plate and phone number belong to different users. Prioritized number plate."
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already has an active parking booking.",
            )

        slot_status, parking_slot = BookParkingSlot(conn)
        if slot_status == 404:
            conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No available parking slots.",
            )
        conn.execute(
            "INSERT INTO logs (id, slotid, starttime, userid) VALUES (?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                parking_slot["id"],
                datetime.now(timezone.utc).isoformat(),
                user_data["id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "userdata": user_data,
        "parking_slot": parking_slot,
        "warning": warning,
    }