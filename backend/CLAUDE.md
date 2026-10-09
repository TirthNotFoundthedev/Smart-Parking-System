# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Layout

The git repo root is the parent `Design Thinking Project` directory. This `backend/` folder is an API-only FastAPI + SQLite app (Python >=3.12, managed with `uv`). The frontend lives in `../frontend` and is out of scope here. `backend/.venv` is a Windows venv (`Scripts/`), so project Python/uv commands run from PowerShell, not WSL.

## Commands (PowerShell, from `backend/`)

```powershell
uv sync                  # create .venv
.\startserver.ps1        # run API (default 127.0.0.1:5000; -Port / -BindHost)
.\run_tests.ps1          # all tests/test*.py, writes result.html (-OpenReport, -ReportPath)
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest tests.test_gate_entry   # plain run
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest tests.test_gate_entry.GateEntryTests.<test_name>   # single test
```

Docs are served at `/docs`; `API_GUIDELINES.md` is the maintained API reference. Update it when endpoints change.

## Architecture

- `src/server/main.py` holds everything: CORS setup (`CORS_ORIGINS` env var, comma separated, default `*`), schema creation at import time, endpoints, the guard login and bearer dependency, and the `CheckUser` / `BookParkingSlot` helpers. Do not define a `main` name in `src/server/__init__.py`, because it would shadow the `server.main` module (tests do `from server import main`).
- Endpoints: `/`, `/health`, `/parking-slots`, `/parking-status`, `POST /guard/login`, `POST /gate-entry` (guard token required), `PUT /sensor/slots/{slot_id}` (public, mimics a hardware sensor).
- Guard login: `user1234` / `pass1234` are module constants in `main.py`. Successful login returns a random token stored in the in-memory `guard_tokens` set. Tokens are lost on restart.
- DB is `backend/parking.db` (gitignored, local). Tables: `parkingslots`, `users`, `logs`. `mockdata.py` is a script that seeds random slots.
- `POST /gate-entry` runs in one `BEGIN IMMEDIATE` transaction: resolve/create user, reject if the user has an open `logs` row (409), book the first slot with `digitalstatus=0 AND physicalstatus=0`, insert an open log row. Any failure rolls back, including a newly created user. `"NA"` means "missing" for plate/phone.
- Tests patch `main.DB_PATH` to a temp database, so they never touch `parking.db`.
