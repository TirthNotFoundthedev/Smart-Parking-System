# Smart Parking System

A low-cost parking system built as a school project: an IR sensor at every slot, small Arduino nodes on an RS485 bus, a gateway, a Python server, and web apps for the guard and drivers. It is designed to be cheap to build (about ₹2,400 for a 10-slot demo) and to scale from one lot to several.

```
IR sensors (5 per node) -> Arduino node -> RS485 bus -> Gateway -> Server (FastAPI + SQLite) -> Guard console
                                                                                            \-> Driver web app (planned)
```

The full design (hardware protocol, booking state machine, API list, data model, parts list and build plan) is in [ARCHITECTURE.md](ARCHITECTURE.md).

## How it works

- Each slot has two statuses. `digitalstatus` is set by bookings, `physicalstatus` by sensors. A car in a slot with no booking raises a guard alert.
- A car arriving at the gate is booked into the first free slot and has 15 minutes to park. Cars that never park are released as no-shows.
- A node that stops answering marks its slots as unavailable and alerts the guard.

## Repository layout

| Folder | What it is |
| --- | --- |
| [`backend/`](backend) | FastAPI + SQLite backend. API reference in [`backend/API_GUIDELINES.md`](backend/API_GUIDELINES.md). |
| [`frontend/`](frontend) | One static site, hostable anywhere: driver page (`user/`), guard console (`guard/`, login required) and sensor simulator with plate cameras (`sensor/`). See [`frontend/README.md`](frontend/README.md). |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System architecture and decisions. |

## Quick start

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). Commands are for PowerShell on Windows.

1. Start the backend (from `backend/`):

```powershell
uv sync
$env:PARKING_GATEWAY_KEY = "dev-key"   # needed for the sensor simulator and plate cameras
.\startserver.ps1                      # http://127.0.0.1:5000, API docs at /docs
```

2. The database starts empty. Add parking slots with `python src/server/mockdata.py` (random slots) or by inserting rows into `parkingslots` in `backend/parking.db`.

3. Serve the frontend (from `frontend/`) and open http://localhost:8080:

```powershell
python -m http.server 8080
```

4. Guard login is `user1234` / `pass1234`. In the sensor simulator enter the gateway key, scan a plate at entry, park the car and watch the guard console and driver page update.

The frontend finds the backend through `frontend/config.js`. For other frontend origins, set `CORS_ORIGINS` on the backend.

## Tests

From `backend/`:

```powershell
.\run_tests.ps1
```

This runs every `tests/test*.py` and writes `result.html`. See [`backend/README.md`](backend/README.md).

## Status

Working: gate entry/exit, bookings with no-show expiry, sensor ingest, alerts, guard console, sensor simulator.

Not built yet: reservations and driver accounts, strikes and bans, driver web app (`/my-slot` exists on the server), guard login, the real Arduino/RS485 firmware and gateway.

Note: the gate and alert endpoints have no authentication yet, so do not expose the server to an untrusted network.
