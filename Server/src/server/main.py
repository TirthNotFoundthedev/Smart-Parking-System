from pathlib import Path
import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

DB_PATH = Path(__file__).resolve().parents[2] / "parking.db"


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
        cur.execute("SELECT * FROM parkingslots")
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

class GateEntry(BaseModel):
    number_plate: str
    phone_number: str = "NA"
    username: str = "NA"

@app.post("/gate-entry")
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

        slot_status, parking_slot = BookParkingSlot(conn)
        if slot_status == 404:
            conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No available parking slots.",
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "userdata": user_data,
        "parking_slot": parking_slot,
        "warning": warning,
    }