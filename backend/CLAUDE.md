# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Layout

The git repo root is the parent `Design Thinking Project` directory; this backend lives in `backend/`. It is a FastAPI + SQLite app (Python >=3.12, managed with `uv`). `backend/.venv` is a Windows venv (`Scripts/`), so project Python/uv commands run from PowerShell, not WSL.

## Commands (PowerShell, from `backend/`)

```powershell
uv sync                  # create .venv
.\startserver.ps1        # run API (default 127.0.0.1:5000; -Port / -BindHost)
.\run_tests.ps1          # all tests/test*.py, writes result.html (-OpenReport, -ReportPath)
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests   # plain run
python -m unittest test_gate_entry.GateEntryTests.<test_name>   # single test (run from tests/ with PYTHONPATH=../src)
```

Docs are served at `/docs`; `API_GUIDELINES.md` is the maintained API reference — update it when endpoints change.

## Architecture

- `src/server/main.py` holds everything: schema creation at import time, endpoints, and the `CheckUser` / `BookParkingSlot` helpers. Do not define a `main` name in `src/server/__init__.py` — it would shadow the `server.main` module (tests do `from server import main`).
- DB is `backend/parking.db` (gitignored, local). Tables: `parkingslots`, `users`, `logs`. `mockdata.py` is a script that seeds random slots.
- `POST /gate-entry` runs in one `BEGIN IMMEDIATE` transaction: resolve/create user, reject if the user has an open `logs` row (409), book the first slot with `digitalstatus=0 AND physicalstatus=0`, insert an open log row. `POST /gate-exit` resolves the user the same way, then in one transaction sets `endtime` on the open log row and resets the slot's `digitalstatus` to 0 (404 if no open row); `GET /logs` reads the table. Any failure rolls back, including a newly created user. `"NA"` means "missing" for plate/phone.
- `src/server/logging_config.py` sets up the `server` logger (console + rotating `logs/server.log`); `main.py` logs requests via middleware and gate-entry events. CORS origins come from `CORS_ORIGINS` (fallback `PARKING_CORS_ORIGINS`, default `*`; methods GET/POST/PUT/OPTIONS; headers Content-Type, Authorization, X-Gateway-Key; no credentials). Slots are booked in `floor, name, id` order.
- `src/server/bookings.py` holds the booking lifecycle, sensor reconciliation, alerts and no-show expiry as functions that take a connection and never commit (so `main.py` endpoints own the transaction). Tables `bookings`, `alerts`, `slot_sensors` are created by `main.init_db()`; tests create the old tables themselves and then call `main.init_db()`. Sensor data lives in `slot_sensors` (not `parkingslots`) so existing positional slot inserts keep working. Gateway endpoints need `PARKING_GATEWAY_KEY`. System design and the build plan are in `../ARCHITECTURE.md`.
- Guard auth: `POST /guard/login` (constants `GUARD_USERNAME`/`GUARD_PASSWORD`, in-memory `guard_tokens`) returns a bearer token. `require_guard` accepts that token OR a valid `X-Gateway-Key` and protects `/gate-entry`, `/gate-exit`, `/logs`, `/bookings`, `/alerts`, `/alerts/{id}/resolve`. Public: `/health`, `/parking-slots` (adds `numberplate`/`booking_status` for assigned slots), `/parking-status` (Rs 20 + Rs 3 per started 10 min), `/availability`, `/my-slot`, `PUT /sensor/slots/{id}`. Tests log in through `/guard/login` in `setUp`. There is no HTML dashboard here; the UIs live in `../frontend/`.
- Tests patch `main.DB_PATH` to a temp database, so they never touch `parking.db`.
