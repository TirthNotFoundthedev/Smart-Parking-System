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
| [`Server/`](Server) | FastAPI + SQLite backend. API reference in [`Server/API_GUIDELINES.md`](Server/API_GUIDELINES.md). |
| [`Gaurd - Frontend/`](Gaurd%20-%20Frontend) | Guard console: gate entry/exit by number plate, live lot map, alerts, history. Static HTML/JS. |
| [`Sensor - Frontend/`](Sensor%20-%20Frontend) | Sensor simulator: imitates the IR sensors, nodes, gateway and plate cameras in a browser. Static HTML/JS. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System architecture and decisions. |

## Quick start

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). Commands are for PowerShell on Windows.

1. Start the server (from `Server/`):

```powershell
uv sync
$env:PARKING_GATEWAY_KEY = "dev-key"   # needed only for the sensor simulator / gateway
.\startserver.ps1                      # http://127.0.0.1:5000, API docs at /docs
```

2. The database starts empty. Add parking slots with `python src/server/mockdata.py` (random slots) or by inserting rows into `parkingslots` in `Server/parking.db`.

3. Serve a frontend. From `Gaurd - Frontend/` and `Sensor - Frontend/` respectively, run one of:

```powershell
python -m http.server 5173 --bind 127.0.0.1   # Guard console  -> http://127.0.0.1:5173
python -m http.server 3000 --bind 127.0.0.1   # Sensor simulator -> http://127.0.0.1:3000
```

4. In the simulator, open **Settings** and enter the gateway key. Then scan a plate at entry, park the car and watch the guard console update. Each frontend's own README has a few scenarios to try.

If the server runs on another address or port, set it under **Server** / **Settings** in each frontend. For other frontend origins, set `PARKING_CORS_ORIGINS` on the server.

## Tests

From `Server/`:

```powershell
.\run_tests.ps1
```

This runs every `tests/test*.py` and writes `result.html`. See [`Server/README.md`](Server/README.md).

## Status

Working: gate entry/exit, bookings with no-show expiry, sensor ingest, alerts, guard console, sensor simulator.

Not built yet: reservations and driver accounts, strikes and bans, driver web app (`/my-slot` exists on the server), guard login, the real Arduino/RS485 firmware and gateway.

Note: the gate and alert endpoints have no authentication yet, so do not expose the server to an untrusted network.
