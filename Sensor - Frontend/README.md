# Sensor Simulator

Browser stand-in for the hardware, so the server and guard console can be built and demoed without Arduinos. No build step, no dependencies. It only uses the public HTTP API, the same calls the real gateway and cameras would make.

## What it imitates

- **Slot nodes + gateway:** every parking slot gets an IR sensor, grouped five per node (same as the hardware plan). Click a sensor to put a car in front of it or remove it. It sends `POST /sensor-events` addressed by node and channel.
- **Node health:** the Online/Offline switch sends `POST /gateway/heartbeat`. While a node is offline, sensor changes are kept locally ("not reported yet") and are all reported when the node comes back, like a real gateway re-reading a node after a reconnect.
- **Entry camera:** scan a plate, `POST /gate-entry`. The result has a button to park the car in the assigned slot (blocks that slot's IR sensor).
- **Exit camera:** scan a plate (pick from cars in the lot or type one), `POST /gate-exit`. If the slot's sensor still sees a car, a button simulates the car leaving.
- **What was sent:** a log of every request and the server's answer.

## Run

1. Start the API with a gateway key (from `Server/`):

```powershell
$env:PARKING_GATEWAY_KEY = "dev-key"
.\startserver.ps1
```

2. Serve this folder on port 3000 (in the server's default CORS allow-list):

```powershell
python -m http.server 3000 --bind 127.0.0.1
```

3. Open http://127.0.0.1:3000, then **Settings**: set the API address if it is not `http://127.0.0.1:5000`, and the gateway key (`dev-key` above). The page registers the slot-to-node mapping automatically; **Register nodes** repeats it.

Run it next to the guard console (`../Gaurd - Frontend`, port 5173) to watch a scenario from both sides.

## Try this

1. Scan a plate at entry, then **Park the car** and watch the guard's slot go from "Assigned, waiting" to "Occupied".
2. Click a free slot's sensor with no booking: the guard gets a "No booking" alert.
3. Take a node offline and park a car on it: its slots become "Sensor offline" for the guard and cannot be assigned.
4. Scan an entry and never park: after 15 minutes the slot is released as a no-show.
