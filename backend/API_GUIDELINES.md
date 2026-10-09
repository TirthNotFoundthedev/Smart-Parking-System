# Parking Server API Guidelines

This guide describes the HTTP API currently implemented by the FastAPI server.
The server is API-only and uses JSON. `POST /gate-entry` requires a guard
login token; the sensor endpoint and the read endpoints are public.

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
| `GET` | `/parking-status` | Active booking, time parked and cost for a number plate. |
| `POST` | `/guard/login` | Log in as the guard and receive a bearer token. |
| `POST` | `/gate-entry` | Find or create a user and book one available slot. Requires a guard token. |
| `PUT` | `/sensor/slots/{slot_id}` | Set a slot's physical occupancy, as a hardware sensor does. Public. |
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
typically serialized as `0` (false) or `1` (true). A slot is eligible for
booking only when both values are `0`.

## Guard login

### `POST /guard/login`

Send a JSON request with `Content-Type: application/json`.

```json
{
  "username": "user1234",
  "password": "pass1234"
}
```

Successful response (`200 OK`):

```json
{
  "token": "b1Xc0...random-url-safe-string"
}
```

Send the token on guarded requests as `Authorization: Bearer <token>`. Tokens
are random and kept in server memory, so they are invalid after a server
restart. Logging in again returns a new token; earlier tokens stay valid until
restart. The credentials are fixed server constants.

Errors: `401 Unauthorized` with `{"detail":"Wrong username or password."}`.

## Sensor

### `PUT /sensor/slots/{slot_id}`

Public endpoint for a hardware sensor. It needs no token. Send:

```json
{
  "occupied": true
}
```

`occupied: true` sets `physicalstatus` to `1`; `false` sets it to `0`. The
response is the updated slot, in the same shape as an item from
`GET /parking-slots`:

```json
{
  "id": "slot-001",
  "name": "SLOT-001",
  "digitalstatus": 0,
  "physicalstatus": 1,
  "floor": "B1"
}
```

Errors: `404 Not Found` with `{"detail":"Slot not found."}` when no slot has
that `slot_id`.

## Gate entry and slot booking

### `POST /gate-entry`

Requires `Authorization: Bearer <token>` from `POST /guard/login`. Without a
valid token the response is `401 Unauthorized`:

```json
{"detail":"Guard login required."}
```

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
| `401 Unauthorized` | Missing, unknown, or malformed guard token. | `{"detail":"Guard login required."}` |
| `409 Conflict` | The user already has an active booking. | `{"detail":"User already has an active parking booking."}` |
| `404 Not Found` | No available parking slot could be booked. | `{"detail":"No available parking slots."}` |
| `422 Unprocessable Entity` | Invalid JSON, missing required `number_plate`, or a field has the wrong type. | FastAPI validation response with a `detail` array. |
| `500 Internal Server Error` | An unexpected server or database error occurred. | Error response; do not assume the request succeeded. |

The frontend should check `response.ok` before treating a request as
successful, and should show the `detail` value (or validation details) for
errors.

## Parking status

### `GET /parking-status?number_plate=ABC123`

Returns the open booking for a number plate (case-insensitive). Cost is
Rs 20 fixed plus Rs 3 for every started 10-minute block.

```json
{
  "slot_name": "SLOT-001",
  "floor": "B1",
  "starttime": "2026-10-09T10:00:00+00:00",
  "minutes_parked": 25,
  "cost": 29
}
```

Errors: `400` for a blank plate, `404` when the plate has no open booking.

## Browser integration and CORS

The server enables FastAPI `CORSMiddleware`. Allowed origins come from the
`CORS_ORIGINS` environment variable, a comma-separated list such as:

```text
CORS_ORIGINS=http://localhost:3000,https://parking.example.com
```

If `CORS_ORIGINS` is unset, it defaults to `*` (any origin). All methods and
headers are allowed. Credentials are not allowed, so the frontend sends the
guard token in the `Authorization` header rather than in cookies. Set
`CORS_ORIGINS` to the frontend's exact origins in any shared or production
deployment. The variable is read when the server starts, so restart the server
after changing it.

Example frontend request:

```javascript
const response = await fetch(`${API_BASE_URL}/gate-entry`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${guardToken}`,
  },
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

## Not currently implemented

The API currently has no endpoint to release a slot, register an exit, view a
user's booking history, or read log records (logs are written on entry only). Do not assume a successful
gate-entry response provides those capabilities.
