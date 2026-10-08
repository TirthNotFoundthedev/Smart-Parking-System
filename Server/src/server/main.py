import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
import hmac
import os
from pathlib import Path
import sqlite3
import time
import uuid
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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

# Browser origins allowed to call the API. Override with a comma-separated
# PARKING_CORS_ORIGINS; the defaults are common local frontend dev servers.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:5173,http://127.0.0.1:5173"
)
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("PARKING_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

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


@app.get("/dashboard/gate-entry", response_class=HTMLResponse)
def gate_entry_dashboard():
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Gate entry test</title>
  <style>
    :root {
      color-scheme: light;
      font: 16px/1.5 system-ui, sans-serif;
      color: #20342d;
      background: #f3f1e9;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; }
    main { width: min(100% - 2rem, 960px); margin: 0 auto; padding: 3rem 0; }
    header { margin-bottom: 2rem; }
    h1 { margin: 0; font-size: clamp(2rem, 5vw, 3rem); line-height: 1.1; }
    h2 { margin: 0 0 1rem; font-size: 1.15rem; }
    p { margin: .65rem 0 0; }
    .intro { max-width: 62ch; color: #43574f; }
    .draft-note { margin-top: 1rem; font-size: .9rem; color: #43574f; }
    .workspace { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); gap: 1rem; align-items: start; }
    .panel { min-width: 0; padding: clamp(1.1rem, 3vw, 1.6rem); border: 1px solid #c9cec4; border-radius: 12px; background: #fffefa; }
    .field { margin: 0 0 1.1rem; }
    label { display: block; margin-bottom: .35rem; font-weight: 650; }
    input {
      width: 100%;
      min-height: 46px;
      padding: .65rem .75rem;
      border: 1px solid #87968e;
      border-radius: 6px;
      color: #20342d;
      background: #fff;
      font: inherit;
    }
    input:focus-visible, button:focus-visible, a:focus-visible {
      outline: 3px solid #9b5a22;
      outline-offset: 3px;
    }
    .hint, .quiet { color: #43574f; font-size: .92rem; }
    .actions { display: flex; flex-wrap: wrap; gap: .7rem; margin-top: 1.4rem; }
    button { min-height: 46px; padding: .65rem 1rem; border: 1px solid #1e5541; border-radius: 6px; font: inherit; font-weight: 650; cursor: pointer; }
    button[type="submit"] { color: #fff; background: #1e5541; }
    button[type="reset"] { color: #20342d; background: transparent; }
    button:disabled { cursor: wait; opacity: .72; }
    .status { min-height: 3rem; margin: 0 0 1rem; color: #43574f; }
    .status[data-state="error"] { color: #8a2f20; font-weight: 600; }
    .warning { margin: 1rem 0; padding: .8rem; border: 1px solid #a9682c; border-radius: 6px; color: #603b19; background: #fff4df; }
    .details { display: grid; grid-template-columns: minmax(7rem, .7fr) minmax(0, 1.3fr); gap: .6rem 1rem; margin: 0; overflow-wrap: anywhere; }
    .details dt { color: #43574f; }
    .details dd { margin: 0; font-weight: 600; }
    a { color: #174a39; }
    /* One column keeps the request and its response together on narrow screens. */
    @media (max-width: 680px) {
      main { padding: 2rem 0; }
      .workspace { grid-template-columns: 1fr; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { scroll-behavior: auto !important; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Gate entry</h1>
      <p class="intro">Send a test entry through the gate-entry API and review the assigned user and parking slot here.</p>
      <p class="draft-note">Draft without direction · ENERGY 1 / RHYTHM 1 / MOTION 1</p>
    </header>
    <div class="workspace">
      <section class="panel" aria-labelledby="form-title">
        <h2 id="form-title">Entry details</h2>
        <form id="gate-entry-form">
          <div class="field">
            <label for="number-plate">Vehicle number plate</label>
            <input id="number-plate" name="number_plate" autocomplete="off" placeholder="For example, ABC123">
          </div>
          <div class="field">
            <label for="phone-number">Phone number</label>
            <input id="phone-number" name="phone_number" type="tel" autocomplete="tel" placeholder="For example, 5551000">
          </div>
          <div class="field">
            <label for="username">Name <span class="quiet">(optional)</span></label>
            <input id="username" name="username" autocomplete="name" placeholder="Name for a new user">
          </div>
          <p class="hint">Enter a plate, a phone number, or both. Blank values are sent as NA.</p>
          <div class="actions">
            <button id="submit-entry" type="submit">Submit gate entry</button>
            <button type="reset">Clear form</button>
          </div>
        </form>
      </section>
      <section class="panel" aria-labelledby="result-title" aria-live="polite">
        <h2 id="result-title">API response</h2>
        <p id="status" class="status" role="status" data-state="idle">No request yet. Submit the form to test gate entry.</p>
        <p id="warning" class="warning" role="alert" hidden></p>
        <dl id="result-details" class="details" hidden></dl>
      </section>
    </div>
    <p class="quiet">The page uses the API on this server. <a href="/docs">Open API documentation</a>.</p>
  </main>
  <script>
    const form = document.getElementById("gate-entry-form");
    const statusMessage = document.getElementById("status");
    const submitButton = document.getElementById("submit-entry");
    const warning = document.getElementById("warning");
    const resultDetails = document.getElementById("result-details");

    function showStatus(message, state) {
      statusMessage.textContent = message;
      statusMessage.dataset.state = state;
    }

    function showResult(payload) {
      const rows = [
        ["Name", payload.userdata.name],
        ["Number plate", payload.userdata.numberplate],
        ["Phone number", payload.userdata.phonenumber],
        ["Assigned slot", payload.parking_slot.name],
        ["Floor", payload.parking_slot.floor],
      ];
      resultDetails.replaceChildren(...rows.flatMap(([label, value]) => {
        const term = document.createElement("dt");
        term.textContent = label;
        const description = document.createElement("dd");
        description.textContent = value;
        return [term, description];
      }));
      resultDetails.hidden = false;
      warning.textContent = payload.warning || "";
      warning.hidden = !payload.warning;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const numberPlate = values.get("number_plate").trim();
      const phoneNumber = values.get("phone_number").trim();
      const username = values.get("username").trim();

      resultDetails.hidden = true;
      warning.hidden = true;
      if (!numberPlate && !phoneNumber) {
        showStatus("Enter a vehicle plate or phone number to continue.", "error");
        form.elements.number_plate.focus();
        return;
      }

      submitButton.disabled = true;
      submitButton.textContent = "Submitting...";
      showStatus("Sending the gate-entry request...", "loading");
      try {
        const response = await fetch("/gate-entry", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            number_plate: numberPlate || "NA",
            phone_number: phoneNumber || "NA",
            username: username || "NA",
          }),
        });
        const payload = await response.json().catch(() => null);
        if (!payload) {
          throw new Error("The server returned an unreadable response.");
        }
        if (!response.ok) {
          const detail = Array.isArray(payload.detail)
            ? payload.detail.map((item) => item.msg).join(" ")
            : payload.detail;
          throw new Error(typeof detail === "string" ? detail : "The gate-entry request failed.");
        }
        showResult(payload);
        showStatus("Gate entry completed.", "success");
      } catch (error) {
        showStatus(
          error instanceof TypeError
            ? "Could not reach the server. Check that the API is running and try again."
            : error.message,
          "error",
        );
      } finally {
        submitButton.disabled = false;
        submitButton.textContent = "Submit gate entry";
      }
    });

    form.addEventListener("reset", () => {
      window.setTimeout(() => {
        resultDetails.replaceChildren();
        resultDetails.hidden = true;
        warning.textContent = "";
        warning.hidden = true;
        showStatus("No request yet. Submit the form to test gate entry.", "idle");
      }, 0);
    });
  </script>
</body>
</html>"""


@app.get("/parking-slots")
def parking_slots():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.*, COALESCE(s.state, 'ok') AS sensor_state
            FROM parkingslots p LEFT JOIN slot_sensors s ON s.slotid = p.id
            """
        )
        rows = cur.fetchall()
        return [dict(row) for row in rows]
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

@app.post("/gate-entry")
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

@app.post("/gate-exit")
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


#! Log history

@app.get("/logs")
def list_logs(user_id: str | None = None, active: bool | None = None, limit: int = 100):
    """Entry/exit history, newest first. `active=true` lists open entries only."""
    query = "SELECT * FROM logs"
    clauses, params = [], []
    if user_id is not None:
        clauses.append("userid = ?")
        params.append(user_id)
    if active is not None:
        clauses.append("endtime IS NULL" if active else "endtime IS NOT NULL")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY starttime DESC LIMIT ?"
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


#! Guard alerts (no guard auth yet; planned with the guard login)

@app.get("/alerts")
def list_alerts(open: bool = True):
    """Guard alerts, newest first. `open=false` lists resolved ones."""
    clause = "resolved_at IS NULL" if open else "resolved_at IS NOT NULL"
    with closing(get_db()) as conn:
        rows = conn.execute(f"SELECT * FROM alerts WHERE {clause} ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


@app.post("/alerts/{alert_id}/resolve")
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

@app.get("/bookings")
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
