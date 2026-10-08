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
typically serialized as `0` (false) or `1` (true). A slot is eligible for
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

CORS is not currently configured by this server. A browser frontend served
from a different origin (for example, a different port such as
`http://localhost:3000`) may have its API request blocked by the browser until
the backend explicitly allows that frontend origin. For local testing, a
same-origin setup or a frontend development-server proxy avoids this; for
separate origins, ask the backend maintainer to configure FastAPI CORS for the
frontend's exact development and production origins. Do not disable browser
security checks.

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

## Not currently implemented

The API currently has no endpoint to release a slot, register an exit, view a
user's booking history, or read log records (logs are written on entry only). Do not assume a successful
gate-entry response provides those capabilities.
