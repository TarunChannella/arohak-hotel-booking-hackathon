# Meridian Grand — Hotel Booking Management System

AROHAK hackathon submission. A hotel booking system with authentication,
role-based access, hotel and room management, availability search, safe booking,
a cancellation workflow with staff review, and a grounded PDF chatbot.

Requirements: [docs/AROHAK_PROBLEM_STATEMENT.md](docs/AROHAK_PROBLEM_STATEMENT.md)
Current state: [PROJECT_STATUS.md](PROJECT_STATUS.md)

## Run

Python 3.10+ is the only requirement. There are no third-party packages, no
build step and no API key.

```bash
python server.py
```

Open <http://127.0.0.1:8000>. The database is created and seeded on first run.

```bash
PORT=8099 python server.py                     # alternate port
HOTEL_DB=/path/to/hotel.db python server.py    # alternate database
```

## Demo accounts

Seeded on first run for evaluation. These are demo credentials for a local
throwaway database, not real secrets. Override the password by setting
`DEMO_PASSWORD` before the first run.

| Role | Email | Password |
|---|---|---|
| ADMIN | `admin@meridiangrand.example` | `Hackathon2026` |
| RECEPTIONIST | `reception@meridiangrand.example` | `Hackathon2026` |
| CUSTOMER | `customer@example.com` | `Hackathon2026` |

New customers can self-register from the sign-in screen. Staff accounts cannot
be self-registered — an administrator creates them via `POST /api/staff`.

## Test

```bash
python -m unittest discover -s tests -v
```

56 tests: authentication and roles, hotel and room management, availability,
booking, concurrency, the cancellation lifecycle, and the grounded chatbot.

## Roles

| Capability | ADMIN | RECEPTIONIST | CUSTOMER |
|---|:--:|:--:|:--:|
| Edit hotel details | ✅ | — | — |
| Create / deactivate rooms | ✅ | — | — |
| Edit room details and availability | ✅ | ✅ | — |
| View all bookings, search and filter | ✅ | ✅ | — |
| Approve / reject cancellation requests | ✅ | ✅ | — |
| Browse rooms and check availability | ✅ | ✅ | ✅ |
| Book a room | — | — | ✅ |
| View own bookings | — | — | ✅ |
| Cancel own booking | — | — | ✅ |

Receptionists deliberately hold no admin-level functionality: they maintain
rooms and bookings but cannot change hotel information or the room inventory.

## API

| Method | Endpoint | Access |
|---|---|---|
| GET | `/api/health` | public |
| POST | `/api/auth/register` | public (CUSTOMER only) |
| POST | `/api/auth/login` | public |
| POST | `/api/auth/logout` | authenticated |
| GET | `/api/auth/me` | public |
| POST | `/api/staff` | ADMIN |
| GET | `/api/users` | ADMIN |
| GET | `/api/hotel` | public |
| PUT | `/api/hotel` | ADMIN |
| GET | `/api/rooms` | public (staff also see inactive rooms) |
| POST | `/api/rooms` | ADMIN |
| PUT | `/api/rooms/{id}` | ADMIN, RECEPTIONIST |
| POST | `/api/rooms/{id}/activate` · `/deactivate` | ADMIN |
| GET | `/api/availability?check_in&check_out&guests` | public |
| POST | `/api/bookings` | CUSTOMER |
| GET | `/api/bookings` | own bookings; staff see all, with `?q=` and `?status=` |
| GET | `/api/bookings/{id}` | owner or staff |
| POST | `/api/bookings/{id}/cancel` | owner or staff |
| GET | `/api/cancellations` | ADMIN, RECEPTIONIST |
| POST | `/api/bookings/{id}/approve-cancellation` · `/reject-cancellation` | ADMIN, RECEPTIONIST |
| POST | `/api/chat` | public |

## Architecture

```
Organization -> Hotel -> Room -> Booking
                          User ->
```

- `db.py` — schema, connections, seed data
- `auth.py` — PBKDF2 password hashing, sessions, role checks
- `hotel.py` — hotel and individual room management
- `booking.py` — availability, atomic booking, cancellation lifecycle
- `rag.py` — grounded retrieval over the hotel PDF
- `server.py` — routing and server-side authorization
- `static/` — role-aware single-page UI

### Notable decisions

**Passwords** are stored as PBKDF2-HMAC-SHA256, 200,000 rounds, with a random
per-user salt, compared with `hmac.compare_digest`. Sessions are opaque 256-bit
tokens held server-side and sent as an `HttpOnly`, `SameSite=Strict` cookie. No
secret is hardcoded anywhere in the repository.

**Authorization is server-side on every route.** The client never declares its
own role; the role is read from the session on each request. The UI hides
controls a role may not use, but hiding is cosmetic — the server rejects the
request regardless.

**Simultaneous bookings are safe.** The availability re-check and the insert run
inside one `BEGIN IMMEDIATE` transaction, so two requests for the last free room
serialise: the second sees the first's row and is rejected. A test races ten
threads at one room and asserts exactly one winner.

**A cancellation request does not release the room.** Staff may reject it, and
the booking then stays confirmed, so the room must stay held until the decision
is made. Approval cancels the booking and frees the room; rejection restores it
to CONFIRMED.

**`organization_id` is present from the start** on users, hotels and bookings,
with a seeded default organization, so the multi-organization extension can be
layered on without rebuilding the MVP.

**The chatbot is extractive, never generative.** Answers are whole sentences
copied verbatim from retrieved PDF passages and returned with section citations.
When retrieval finds nothing strong enough, it says the information is not
available rather than inventing one. This makes hallucination structurally
impossible and needs no API key.

**The UI never uses `innerHTML`.** All text is inserted with `textContent`, so
guest-supplied values such as names cannot inject markup.

## Cancellation rules

- A customer may cancel directly until one day before check-in. For a
  20 September check-in, direct cancellation is available until 19 September.
- After that deadline, cancelling submits a request for staff review, and the
  booking moves to `CANCELLATION_REQUESTED` while continuing to hold the room.
- Staff approve (booking becomes `CANCELLED`, room released) or reject (booking
  returns to `CONFIRMED`).
- A cancelled booking cannot be cancelled again, and a pending request cannot be
  submitted twice.

## Demo questions for the chatbot

- What time is check-in?
- Is parking free?
- What is the cancellation policy?
- Is Wi-Fi free?
- Does the hotel have a casino? *(shows the grounded refusal)*
