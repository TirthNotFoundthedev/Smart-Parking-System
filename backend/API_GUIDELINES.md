# Parking Server API Guidelines

This guide describes the HTTP API implemented by the FastAPI server in
`src/server/main.py` (booking logic in `bookings.py`). The server is API-only
and uses JSON. Interactive docs are at `{BASE_URL}/docs` and the schema at
`{BASE_URL}/openapi.json`.

## Base URL

Use the host and port printed when the server starts, for example
`http://127.0.0.1:5000` (`startserver.ps1 -Port` changes it). Do not assume
another service on the same machine is the parking API. Paths below are
relative to the base URL.

## Access levels

| Level | How to authenticate | Endpoints |
| --- | --- | --- |
| Public | Nothing | `GET /`, `/health`, `/parking-slots`, `/parking-status`, `/availability`, `/my-slot`, `PUT /sensor/slots/{slot_id}` |
| Guard | `Authorization: Bearer <token>` from `POST /guard/login`, **or** a valid `X-Gateway-Key` | `POST /gate-entry`, `POST /gate-exit`, `GET /logs`, `GET /bookings`, `GET /alerts`, `POST /alerts/{id}/resolve` |
| Gateway | `X-Gateway-Key: <PARKING_GATEWAY_KEY>` | `POST /gateway/slot-map`, `POST /sensor-events`, `POST /gateway/heartbeat` |

A guard endpoint called without a valid token or gateway key returns
`401 Unauthorized`:

```json
{"detail":"Guard login required."}
```

Accepting the gateway key on guard endpoints lets hardware, such as the
plate-reader simulator, call gate entry and exit without a guard login.
Gateway endpoints return `401 {"detail":"Invalid gateway key."}` for a bad or
missing key and `503` when `PARKING_GATEWAY_KEY` is not set on the server.

## Endpoint list

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/` | Public | Basic greeting. |
| `GET` | `/health` | Public | Check the API is responding. |
| `GET` | `/parking-slots` | Public | All slots, with the holder's plate when assigned. |
| `GET` | `/parking-status?number_plate=` | Public | Active parking, minutes parked and cost for a plate. |
| `GET` | `/availability` | Public | Free and total slots per floor. |
| `GET` | `/my-slot?plate=` | Public | Where did I park (rate limited). |
| `PUT` | `/sensor/slots/{slot_id}` | Public | Set a slot's physical occupancy (simulator/hardware shortcut). |
| `POST` | `/guard/login` | Public | Exchange guard credentials for a bearer token. |
| `POST` | `/gate-entry` | Guard | Find or create a user and book a slot. |
| `POST` | `/gate-exit` | Guard | Close the user's open booking and free the slot. |
| `GET` | `/logs` | Guard | Entry/exit history. |
| `GET` | `/bookings` | Guard | Bookings with driver and slot attached. |
| `GET` | `/alerts` | Guard | Open or resolved alerts. |
| `POST` | `/alerts/{alert_id}/resolve` | Guard | Resolve an open alert. |
| `POST` | `/gateway/slot-map` | Gateway | Map slots to sensor node and channel. |
| `POST` | `/sensor-events` | Gateway | Push slot occupancy readings. |
| `POST` | `/gateway/heartbeat` | Gateway | Report which sensor nodes are online. |

## Health

`GET /health` returns `{"status": "ok"}`.

## Parking slots

### `GET /parking-slots`

Every slot, including occupied and unavailable ones. The array may be empty.

```json
[
  {
    "id": "slot-001", "name": "SLOT-001", "digitalstatus": 0,
    "physicalstatus": 0, "floor": "B1", "sensor_state": "ok",
    "numberplate": null, "booking_status": null
  },
  {
    "id": "slot-002", "name": "SLOT-002", "digitalstatus": 1,
    "physicalstatus": 0, "floor": "B1", "sensor_state": "ok",
    "numberplate": "ABC123", "booking_status": "assigned"
  }
]
```

- `digitalstatus` / `physicalstatus` are `0` or `1`. A slot is bookable only
  when both are `0` and its sensor state is `ok`.
- `sensor_state` is `ok`, `unknown` (node offline) or `fault`.
- `numberplate` and `booking_status` (`assigned` or `parked`) come from the
  active booking when `digitalstatus` is `1`; both are `null` otherwise.

### `GET /availability`

Free and total slots per floor. Slots with a non-`ok` sensor do not count as
free.

```json
[{"floor": "B1", "total": 5, "free": 3}]
```

## Parking status and driver lookup

### `GET /parking-status?number_plate=ABC123`

Public. Finds the open log row for a number plate (case-insensitive). Cost is
Rs 20 fixed plus Rs 3 for every started 10-minute block.

```json
{
  "slot_name": "SLOT-001",
  "floor": "B1",
  "numberplate": "ABC123",
  "starttime": "2026-10-09T10:00:00+00:00",
  "minutes_parked": 25,
  "cost": 29
}
```

Errors: `400` for a blank or `NA` plate, `404` when the plate has no open entry.

### `GET /my-slot?plate=ABC123`

Public, limited to 10 lookups per minute per client (`429` after that).

```json
{
  "parking_slot": {"id": "slot-001", "name": "SLOT-001", "digitalstatus": 1, "physicalstatus": 0, "floor": "B1"},
  "booking_status": "assigned",
  "expires_at": "2026-10-09T10:15:00+00:00",
  "directions": "Go to floor B1 and look for SLOT-001."
}
```

`expires_at` is only set while the booking is `assigned`. Error: `404` when the
plate has no active booking.

## Guard login

### `POST /guard/login`

```json
{"username": "user1234", "password": "pass1234"}
```

Response (`200 OK`): `{"token": "<random url-safe string>"}`. Send it as
`Authorization: Bearer <token>`. Tokens are kept in server memory, so they are
invalid after a restart; earlier tokens stay valid until then. The credentials
are fixed server constants (`GUARD_USERNAME`, `GUARD_PASSWORD`).

Error: `401` with `{"detail":"Wrong username or password."}`.

## Gate entry

### `POST /gate-entry` (guard)

| Field | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `number_plate` | string | Yes | none | Send `"NA"` for phone-only lookup. |
| `phone_number` | string | No | `"NA"` | |
| `username` | string | No | `"NA"` | Used only when a new user is created. |

An empty, whitespace-only or `"NA"` (case-sensitive) value counts as missing;
at least one usable identifier is required. The server matches an existing user
by both identifiers, falls back to whichever one matches, and creates a user if
neither does. If plate and phone belong to different users, the plate-matched
user is used and `warning` explains it (still `200 OK`).

In one transaction the server books the first free slot (ordered by floor,
name, id), writes an open `logs` row, and creates a walk-in booking with status
`assigned` that expires after 15 minutes unless the sensor sees the car park.
A user with an open booking cannot enter again. If no slot can be booked the
request fails and a newly created user is rolled back.

Response (`200 OK`):

```json
{
  "userdata": {"id": "user-id", "name": "Alex", "numberplate": "ABC123", "phonenumber": "5551000"},
  "parking_slot": {"id": "slot-001", "name": "SLOT-001", "digitalstatus": 1, "physicalstatus": 0, "floor": "B1"},
  "warning": null
}
```

| Status | Meaning |
| --- | --- |
| `400` | No usable plate or phone: `Please provide either number plate or phone number.` |
| `401` | `Guard login required.` |
| `404` | `No available parking slots.` |
| `409` | `User already has an active parking booking.` |
| `422` | Invalid JSON or wrong field types. |

## Gate exit

### `POST /gate-exit` (guard)

Body: `number_plate` (required string) and `phone_number` (default `"NA"`),
resolved like gate entry. Sets `endtime` on the open log row, resets the slot's
`digitalstatus` to 0 and marks the booking `completed`.

```json
{
  "userdata": {"id": "user-id", "name": "Alex", "numberplate": "ABC123", "phonenumber": "5551000"},
  "parking_slot": {"id": "slot-001", "name": "SLOT-001", "digitalstatus": 0, "physicalstatus": 0, "floor": "B1"},
  "log": {"id": "log-id", "slotid": "slot-001", "starttime": "...", "endtime": "...", "userid": "user-id"},
  "warning": null
}
```

Errors: `400` no usable identifier; `401` guard login required; `404` with
`User not found.` or `User has no active parking booking.`.

## Logs, bookings and alerts (guard)

### `GET /logs?user_id=&active=&limit=100`

Log rows newest first, each with the user's `numberplate` added.
`active=true` lists open entries, `active=false` closed ones. `limit` is
clamped to 1..1000.

```json
[{"id": "log-id", "slotid": "slot-001", "starttime": "...", "endtime": null, "userid": "user-id", "numberplate": "ABC123"}]
```

### `GET /bookings?active=&limit=100`

Newest first. `active=true` lists `assigned` and `parked` bookings,
`active=false` the finished ones (`completed`, `noshow`, `cancelled`).

```json
[{
  "id": "booking-id", "kind": "walkin", "status": "assigned",
  "start_time": "...", "expires_at": "...",
  "userid": "user-id", "username": "Alex", "numberplate": "ABC123", "phonenumber": "5551000",
  "slotid": "slot-001", "slotname": "SLOT-001", "floor": "B1"
}]
```

### `GET /alerts?open=true`

Alerts newest first (`open=false` lists resolved ones). Each has `id`,
`slotid`, `type` (`unbooked_car`, `node_offline`), `detail`, `created_at`,
`resolved_at`.

### `POST /alerts/{alert_id}/resolve`

Returns `{"resolved": "<id>"}`; `404` when there is no open alert with that id.

## Sensors and gateway

### `PUT /sensor/slots/{slot_id}` (public)

Body `{"occupied": true}`. Applies the same reconciliation as a sensor event
(an `assigned` booking becomes `parked`; a car in an unbooked slot opens an
alert) and returns the updated slot row. `404 {"detail":"Slot not found."}`
for an unknown slot.

### `POST /gateway/slot-map` (gateway)

Body: list of `{"slot_id", "node", "channel"}`. Returns `{"mapped": n}`.
`404` for an unknown slot; `409` when a node/channel is already mapped to
another slot.

### `POST /sensor-events` (gateway)

Body: list of `{"slot_id"?, "node"?, "channel"?, "occupied"}`. A reading is
addressed by `slot_id` or by `node` + `channel`. Returns
`{"applied": [{"index", "slot_id", "outcome"}], "rejected": [{"index", "reason"}]}`.
`outcome` is `parked`, `unbooked`, `occupied` or `vacated`.

### `POST /gateway/heartbeat` (gateway)

Body: `{"nodes": [{"node": 1, "online": true}]}`. An offline node marks its
slots `unknown` (not assignable) and opens a `node_offline` alert; coming back
online restores them. Returns `{"nodes": n}`.

## Booking lifecycle

`assigned` (slot held, 15 minutes) -> `parked` (sensor saw the car) ->
`completed` (gate exit). An `assigned` booking that is not parked in time
becomes `noshow` and the slot is released (checked every 30 seconds, see
`PARKING_EXPIRY_INTERVAL`).

## CORS and browser integration

Allowed origins come from `CORS_ORIGINS` (comma-separated), falling back to
`PARKING_CORS_ORIGINS`, and default to `*`. Allowed methods are `GET`, `POST`,
`PUT`, `OPTIONS`; allowed headers are `Content-Type`, `Authorization` and
`X-Gateway-Key`. Credentials are not allowed, so the guard token travels in the
`Authorization` header, not in cookies. Set `CORS_ORIGINS` to the exact
frontend origins for a shared deployment; it is read at startup.

```javascript
const response = await fetch(`${API_BASE_URL}/gate-entry`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${guardToken}` },
  body: JSON.stringify({ number_plate: "ABC123", phone_number: "5551000", username: "Alex" }),
});
const result = await response.json();
if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Validation failed.");
```

## Environment variables

| Variable | Purpose |
| --- | --- |
| `PARKING_GATEWAY_KEY` | Secret for `X-Gateway-Key`. Gateway endpoints return 503 without it. |
| `CORS_ORIGINS` / `PARKING_CORS_ORIGINS` | Allowed browser origins (default `*`). |
| `PARKING_EXPIRY_INTERVAL` | Seconds between no-show sweeps (default 30). |
