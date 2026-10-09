# Guard Console

Static web UI for the parking guard. No build step, no dependencies.

## Run

1. Start the API (from `Server/`): `.\startserver.ps1` (default `http://127.0.0.1:5000`).
2. Serve this folder on port 5173 (already in the server's CORS allow-list):

```powershell
python -m http.server 5173 --bind 127.0.0.1
```

3. Open http://127.0.0.1:5173. If the API is elsewhere, use **Server** (top right) to set its address; it is remembered in the browser. For another frontend origin, set `PARKING_CORS_ORIGINS` on the server.

## What the guard can do

- **Entry:** type the plate (name and phone optional), get the assigned slot to tell the driver. The slot is held for 15 minutes.
- **Exit:** type the plate, or click an occupied slot tile / "Check out" in the table.
- **Lot map:** live per-slot state (refreshes every 5 s): free, assigned and waiting, occupied, car with no booking, sensor offline.
- **Alerts:** unbooked cars and offline sensor nodes, with Resolve.
- **Cars in lot / History:** who is parked where, and finished visits including no-shows.

Alerts and gate actions have no guard login yet (planned, see `../ARCHITECTURE.md`).
