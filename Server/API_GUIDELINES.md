# Parking Server API Guidelines

This guide describes the HTTP API currently implemented by the FastAPI server.
The API uses JSON and has no authentication configured.

## Base URL

Use the host and port printed when the server starts. For example:

```text
http://127.0.0.1:5000
```

The project has also been run on port `5050`; use whichever port belongs to
this server instance. Do not assume that another service on the same machine
is the parking API.

Interactive API documentation is available at `{BASE_URL}/docs`, and the
OpenAPI schema is at `{BASE_URL}/openapi.json`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check whether the API is responding. |
| `GET` | `/parking-slots` | List all parking slots. |
| `POST` | `/gate-entry` | Find or create a user and book one available slot. |
| `POST` | `/gate-exit` | Close the user's active booking and free the slot. |
| `GET` | `/logs` | List entry/exit log rows, newest first. |
| `GET` | `/dashboard/gate-entry` | Browser test page that submits to `POST /gate-entry`. |
| `GET` | `/` | Basic server greeting. |

Paths are relative to the base URL. For example:
`GET http://127.0.0.1:5000/parking-slots`.

## Health check

### `GET /health`

Successful response (`200 OK`):

```json
{
  "status": "ok"
}
```

## List parking slots

### `GET /parking-slots`

Returns every row in the parking-slots table, including occupied and
unavailable spaces. The result is an array and may be empty.

Example (`200 OK`):

```json
[
  {
    "id": "slot-001",
    "name": "SLOT-001",
    "digitalstatus": 0,
    "physicalstatus": 0,
    "floor": "B1"
  },
  {
    "id": "slot-002",
    "name": "SLOT-002",
    "digitalstatus": 1,
    "physicalstatus": 0,
    "floor": "B1"
  }
]
```

`digitalstatus` and `physicalstatus` are SQLite boolean values and are
typically serialized as `0` (false) or `1` (true). `sensor_state` is `ok`,
`unknown` (node offline) or `fault`; slots without a sensor mapping report `ok`. A slot is eligible for
booking only when both values are `0`.

## Gate entry and slot booking

### `GET /dashboard/gate-entry`

Opens a browser form for manually testing gate entry. The page sends its JSON
request to `POST /gate-entry` and displays that endpoint's success or error
response; it does not implement separate user lookup or slot-booking logic.
The design read is a calm test utility for checking a gate-entry request,
with the form and response paired so the outcome stays beside its input. The
page is a draft without supplied design direction at ENERGY 1 / RHYTHM 1 /
MOTION 1. Its paper-and-ink palette keeps the tool quiet, with green reserved
for submission; the system font avoids a remote font dependency.

### `POST /gate-entry`

Send a JSON request with `Content-Type: application/json`.

| Field | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `number_plate` | string | Yes | — | This is compulsary. |
| `phone_number` | string | No | `"NA"` | Use `"NA"` when no phone number is supplied. |
| `username` | string | No | `"NA"` | Used when creating a new user; existing user data is returned unchanged. |

At least one usable identifier is required. An empty value, whitespace-only
value, or `"NA"` (case-sensitive) is treated as missing during lookup. Keep
the required `number_plate` field in the request even for phone-only lookup:

```json
{
  "number_plate": "NA",
  "phone_number": "5551000"
}
```

Trim plate and phone values in the frontend before sending them. The server
trims values for lookup, but if it creates a new user it stores the submitted
values as-is. Send the missing-value marker exactly as `"NA"`.

The server matches an existing user by both identifiers if possible. If only
one of the provided identifiers matches, it uses that user. If the plate and
phone match different users, the server prioritizes the plate-matched user and
includes a warning. If neither matches, it creates a user and then attempts to
book a slot.

A slot is bookable when `digitalstatus` and `physicalstatus` are both `0`.
Bookable slots are assigned in order of `floor`, then `name`, then `id`.
On success the server changes `digitalstatus` to `1` and writes an open row
(`endtime` NULL) to the `logs` table. A user who already has an open log row
cannot book again until it is closed. If no slot can be booked,
the request fails and a newly created user is rolled back with the booking.

### Successful response

Response (`200 OK`):

```json
{
  "userdata": {
    "id": "user-id",
    "name": "Alex",
    "numberplate": "ABC123",
    "phonenumber": "5551000"
  },
  "parking_slot": {
    "id": "slot-001",
    "name": "SLOT-001",
    "digitalstatus": 1,
    "physicalstatus": 0,
    "floor": "B1"
  },
  "warning": null
}
```

When the plate and phone identify different existing users, `warning` contains
this message and `userdata` belongs to the plate-matched user:

```json
"warning": "Conflict: Number plate and phone number belong to different users. Prioritized number plate."
```

The conflict is a successful gate-entry response (`200 OK`), not an HTTP
`409 Conflict`.

### Example request

```http
POST /gate-entry
Content-Type: application/json
```

```json
{
  "number_plate": "ABC123",
  "phone_number": "5551000",
  "username": "Alex"
}
```

### Errors

| HTTP status | Meaning | Example response |
| --- | --- | --- |
| `400 Bad Request` | Both identifiers are absent or treated as `"NA"`. | `{"detail":"Please provide either number plate or phone number."}` |
| `409 Conflict` | The user already has an active booking. | `{"detail":"User already has an active parking booking."}` |
| `404 Not Found` | No available parking slot could be booked. | `{"detail":"No available parking slots."}` |
| `422 Unprocessable Entity` | Invalid JSON, missing required `number_plate`, or a field has the wrong type. | FastAPI validation response with a `detail` array. |
| `500 Internal Server Error` | An unexpected server or database error occurred. | Error response; do not assume the request succeeded. |

The frontend should check `response.ok` before treating a request as
successful, and should show the `detail` value (or validation details) for
errors.

## Browser integration and CORS

CORS is enabled for `GET` and `POST` from these origins by default:
`http://localhost:3000`, `http://127.0.0.1:3000`, `http://localhost:5173`
and `http://127.0.0.1:5173`. Set `PARKING_CORS_ORIGINS` to a comma-separated
list of exact origins to replace them (for example the production frontend).
Requests from other origins are blocked by the browser. Do not disable
browser security checks.

Example frontend request:

```javascript
const response = await fetch(`${API_BASE_URL}/gate-entry`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    number_plate: "ABC123",
    phone_number: "5551000",
    username: "Alex",
  }),
});

const result = await response.json();

if (!response.ok) {
  throw new Error(
    typeof result.detail === "string"
      ? result.detail
      : "The request failed validation. Check the submitted fields."
  );
}

if (result.warning) {
  console.warn(result.warning);
}

console.log("Assigned slot:", result.parking_slot);
```

## Gate exit

### `POST /gate-exit`

Send JSON with the same identifiers as gate entry (`number_plate` required,
`phone_number` optional, `"NA"` meaning missing). The user is resolved the
same way, including the plate-priority conflict warning. The server then, in
one transaction, sets `endtime` on the user's open `logs` row and sets the
slot's `digitalstatus` back to `0`. The user can enter again afterwards, which
writes a new log row.

Response (`200 OK`):

```json
{
  "userdata": { "id": "user-id", "name": "Alex", "numberplate": "ABC123", "phonenumber": "5551000" },
  "parking_slot": { "id": "slot-001", "name": "SLOT-001", "digitalstatus": 0, "physicalstatus": 0, "floor": "B1" },
  "log": { "id": "log-id", "slotid": "slot-001", "starttime": "2026-10-08T10:00:00+00:00", "endtime": "2026-10-08T12:00:00+00:00", "userid": "user-id" },
  "warning": null
}
```

| HTTP status | Meaning |
| --- | --- |
| `400 Bad Request` | Both identifiers are absent or `"NA"`. |
| `404 Not Found` | `"User not found."` or `"User has no active parking booking."` |
| `422 Unprocessable Entity` | Invalid JSON or field types. |

## Entry/exit log

Each entry writes a `logs` row with `starttime` (UTC ISO 8601) and no
`endtime`; the matching exit fills in `endtime`. One row is one parking visit.

### `GET /logs`

Returns an array of log rows, newest first (`id`, `slotid`, `starttime`,
`endtime`, `userid`). Optional query parameters: `user_id`, `active`
(`true` = open entries only, `false` = completed visits only), and `limit`
(default 100, clamped to 1-1000).

## Bookings and no-shows

`POST /gate-entry` also writes a `bookings` row (`kind: walkin`, status
`assigned`) with `expires_at` 15 minutes ahead. A background task (every
`PARKING_EXPIRY_INTERVAL` seconds, default 30) marks assigned bookings that
were never parked as `noshow`, sets the slot's `digitalstatus` back to `0` and
closes the log row. When the sensor reports the car, the booking becomes
`parked` and never expires; `POST /gate-exit` sets it to `completed`.

## Driver endpoints

### `GET /my-slot?plate=ABC123`

Returns the active booking for the plate: `parking_slot`, `booking_status`
(`assigned` or `parked`), `expires_at` (only while `assigned`) and a
`directions` string. `404` when the plate is unknown or has no active booking;
`429` after 10 lookups per minute from one client.

### `GET /availability`

Array of `{floor, total, free}`. A slot is free only when both statuses are `0`
and its sensor is not `unknown`/`fault`.

## Gateway endpoints

These require the `X-Gateway-Key` header to equal the `PARKING_GATEWAY_KEY`
environment variable. They return `503` when the variable is unset and `401`
for a wrong key.

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| `POST` | `/gateway/slot-map` | `[{slot_id, node, channel}]` | Map slots to Arduino node + sensor channel. `409` if the node/channel is taken, `404` for an unknown slot. |
| `POST` | `/sensor-events` | `[{slot_id \| node+channel, occupied}]` | Write `physicalstatus`. Returns `{applied: [{index, slot_id, outcome}], rejected: [...]}`. Outcome is `parked`, `unbooked`, `occupied` or `vacated`. |
| `POST` | `/gateway/heartbeat` | `{nodes: [{node, online}]}` | Offline nodes make their slots `unknown` (not assignable) and raise a `node_offline` alert. |

An occupied slot with no active booking raises an `unbooked_car` alert, which
clears when the slot is vacated.

## Booking list

`GET /bookings` returns bookings with the driver and slot attached (`kind`,
`status`, `start_time`, `expires_at`, `username`, `numberplate`, `phonenumber`,
`slotid`, `slotname`, `floor`), newest first. `active=true` lists assigned or
parked cars; `active=false` lists finished ones. `limit` defaults to 100.

## Guard alerts

`GET /alerts` lists open alerts, newest first (`?open=false` for resolved
ones). `POST /alerts/{id}/resolve` closes one (`404` if not open). These have
no authentication yet; guard login is a later step.

## Not currently implemented

Reservations, driver accounts, strikes and bans, and guard login (see
`../ARCHITECTURE.md`).
