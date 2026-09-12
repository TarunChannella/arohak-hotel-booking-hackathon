# Meridian Grand — Hotel Booking Management System

AROHAK hackathon submission. A hotel booking system with authentication,
role-based access, hotel and room management, availability search, safe booking,
a cancellation workflow with staff review, multi-organization isolation, a
controlled natural-language booking assistant, and a PDF-grounded chatbot.

Requirements: [docs/AROHAK_PROBLEM_STATEMENT.md](docs/AROHAK_PROBLEM_STATEMENT.md)
Current state: [PROJECT_STATUS.md](PROJECT_STATUS.md)

## Install

Python 3.10+ and one dependency, `pypdf`, for extracting text from the hotel PDF.

```bash
python -m pip install -r requirements.txt
```

## Run

```bash
python server.py
```

Open <http://127.0.0.1:8000>. On first run the database is created and seeded, and
`data/AROHAK_Hotel_Information_For_RAG.pdf` is ingested: its text is extracted,
split into chunks that keep their heading and page number, and indexed for the
chatbot.

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

181 tests: authentication and roles, hotel and room management, availability,
booking, concurrency, the cancellation lifecycle, dashboards, PDF ingestion and
upload validation, the grounded chatbot, the controlled booking
assistant, multi-organization isolation and per-hotel PDF retrieval. The
ingestion tests run the actual supplied PDF through the real extraction path.

## Roles

`ADMIN` is the original single-hotel name for `ORGANIZATION_ADMIN`; both have
the same rights and the seeded demo account keeps working.

| Capability | PRODUCT_ADMIN | ORGANIZATION_ADMIN / ADMIN | RECEPTIONIST | CUSTOMER |
|---|:--:|:--:|:--:|:--:|
| Create and manage organizations | ✅ | — | — | — |
| See every organization | ✅ | — | — | — |
| Add hotels to own organization | ✅ | ✅ | — | — |
| Assign receptionists to hotels | ✅ | ✅ | — | — |
| Edit hotel details | ✅ | ✅ | — | — |
| Create / deactivate rooms | ✅ | ✅ | — | — |
| Edit room details and availability | ✅ | ✅ | ✅ | — |
| View bookings, search and filter | ✅ | ✅ | ✅ | — |
| Approve / reject cancellation requests | ✅ | ✅ | ✅ | — |
| Replace the hotel PDF | ✅ | ✅ | — | — |
| Browse hotels and check availability | ✅ | ✅ | ✅ | ✅ |
| Book a room | — | — | — | ✅ |
| View and cancel own bookings | — | — | — | ✅ |

A receptionist sees only the hotels assigned to it, and an organization admin
only its own organization. Staff outside an organization get no rows rather
than another organization's data.

Receptionists deliberately hold no admin-level functionality: they maintain
rooms and bookings on their assigned hotels but cannot change hotel
information, the room inventory, the hotel PDF, or anything in another hotel.

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
| GET | `/api/organizations` | public lists active; staff see their own, PRODUCT_ADMIN sees all |
| POST | `/api/organizations` | PRODUCT_ADMIN |
| PUT | `/api/organizations/{id}` | PRODUCT_ADMIN |
| GET | `/api/hotels?organization_id=` | scoped to the caller |
| POST | `/api/hotels` | PRODUCT_ADMIN, ORGANIZATION_ADMIN |
| GET | `/api/hotels/{id}` · `/rooms` | scoped to the caller |
| PUT | `/api/hotels/{id}` | admins, own organization only |
| POST | `/api/assignments` | admins — assign a receptionist to a hotel |
| POST | `/api/assignments/remove` | admins — unassign |
| GET | `/api/users?role=` | admins — staff in their own organization |
| GET | `/api/hotel` | public — the default hotel |
| PUT | `/api/hotel` | ADMIN |
| GET | `/api/rooms` | public (staff also see inactive rooms) |
| POST | `/api/rooms` | ADMIN |
| PUT | `/api/rooms/{id}` | ADMIN, RECEPTIONIST |
| POST | `/api/rooms/{id}/activate` · `/deactivate` | ADMIN |
| GET | `/api/availability?check_in&check_out&guests&hotel_id` | public |
| POST | `/api/bookings` | CUSTOMER |
| GET | `/api/bookings` | own bookings; staff see all, with `?q=` and `?status=` |
| GET | `/api/bookings/{id}` | owner or staff |
| POST | `/api/bookings/{id}/cancel` | owner or staff |
| GET | `/api/cancellations` | ADMIN, RECEPTIONIST |
| POST | `/api/bookings/{id}/approve-cancellation` · `/reject-cancellation` | ADMIN, RECEPTIONIST |
| GET | `/api/hotel/document?hotel_id=` | indexed PDF metadata for one hotel |
| POST | `/api/hotel/document` | ADMIN — replace the hotel PDF and re-index |
| POST | `/api/assistant` | CUSTOMER — booking assistant; takes `hotel_id` |
| POST | `/api/chat` | public — grounded PDF chatbot; takes `hotel_id` |

## Architecture

```
Organization -> Hotel -> Room -> Booking
                          User ->
```

- `db.py` — schema, connections, seed data
- `auth.py` — PBKDF2 password hashing, sessions, role checks
- `hotel.py` — hotel and individual room management
- `booking.py` — availability, atomic booking, cancellation lifecycle
- `organization.py` — organizations, multi-hotel support, receptionist assignments
- `ingest.py` — PDF extraction, chunking, validation and indexing
- `rag.py` — grounded retrieval over the extracted PDF chunks
- `assistant.py` — controlled booking assistant and its tool layer
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

**Organizations are isolated at the query level, not in the UI.** Every staff
read and write is filtered by the hotels the acting user may reach:
`accessible_hotel_ids()` returns `None` for a product admin, the assigned
hotels for a receptionist, and the organization's hotels for an organization
admin. A request for another organization's hotel raises an authorization
failure, and a listing returns no rows rather than someone else's data.

**The PDF is the source of truth for the chatbot.** On startup `ingest.py`
extracts the text of the hotel's PDF with pypdf, splits it at its own headings
into chunks that keep their page number, and builds the retrieval index from
those chunks. There is no hand-written knowledge file. The JSON under
`data/index_cache/` is a derived cache keyed by the PDF's SHA-256; delete it and
the next start rebuilds it from the PDF.

**The chatbot is extractive, never generative.** Answers are whole sentences
copied verbatim from the extracted PDF text and returned with the section
heading and page number they came from. When retrieval finds nothing strong
enough, it says the information is not available rather than inventing one. This
makes hallucination structurally impossible and needs no API key.

**Replacing the PDF re-indexes that hotel.** An admin uploads a replacement from
the Hotel PDF tab. The upload is rejected unless it is present, within the 10 MB
limit, carries a `%PDF-` signature and a `.pdf` name, and yields extractable
text — a scanned image PDF is refused with an explanation rather than silently
indexed as empty. The filename is reduced to a safe basename, so
`../../server.py` cannot escape the documents directory, and the file is written
to a staging path and only swapped in once extraction succeeds, so a bad upload
leaves the working index untouched. Each hotel has its own PDF, index and cache,
keyed by hotel id, and a retriever only ever reads its own hotel's document.

**PDF content never determines availability.** Room inventory, availability and
bookings come from SQLite. Replacing the PDF changes what the chatbot knows and
nothing about what can be booked.

**The booking assistant has no database access.** `assistant.py` may only call
the seven methods on `ToolLayer` — `get_hotel`, `search_rooms`,
`check_availability`, `get_booking`, `list_bookings`, `create_booking`,
`cancel_booking` — and every one of them is scoped to the signed-in user, so the
assistant cannot read or change another customer's data. It never invents
availability: every room it names came back from a live search in that turn. It
always asks for explicit confirmation before booking or cancelling, and the
pending action is held server-side keyed by user id, so a client cannot forge a
confirmation for an action that was never proposed.

**The UI never uses `innerHTML`.** All text is inserted with `textContent`, so
guest-supplied values such as names cannot inject markup.

## Customer flow

Organization → Hotel → Rooms → Availability → Booking.

The Find Room tab has an organization selector and a hotel selector. The chosen
pair is kept in `localStorage`, so it survives a refresh, and is sent with every
room search, booking, Booking Assistant turn and Hotel Information question.
A selected hotel is never silently replaced by the default: an unknown,
inactive or out-of-scope hotel is refused by the server.

## Per-hotel PDF knowledge

Each hotel has its own PDF, index and cache, keyed by hotel id. `/api/chat`
takes a `hotel_id` and answers only from that hotel's document, citing the
hotel name, section heading and page. A hotel with no PDF says so plainly
rather than borrowing another hotel's. Only the seeded hotel receives the
supplied document; a newly created hotel starts with no knowledge base until an
admin uploads one. Replacing one hotel's PDF rebuilds that hotel's index alone.

## Cancellation rules

- A customer may cancel directly until one day before check-in. For a
  20 September check-in, direct cancellation is available until 19 September.
- After that deadline, cancelling submits a request for staff review, and the
  booking moves to `CANCELLATION_REQUESTED` while continuing to hold the room.
- Staff approve (booking becomes `CANCELLED`, room released) or reject (booking
  returns to `CONFIRMED`).
- A cancelled booking cannot be cancelled again, and a pending request cannot be
  submitted twice.

## Booking assistant examples

Sign in as a customer and open the Booking assistant tab.

- `I need a room in Mumbai for 2 people from Sept 20 to Sept 23.` → lists real
  availability and asks you to confirm
- `yes` → books it
- `Show my upcoming bookings`
- `Cancel my booking` → explains whether it cancels outright or becomes a staff
  request, then asks you to confirm

## Demo questions for the chatbot

- What time is check-in? *(answers 2:00 PM, citing the check-in policy)*
- Can four guests stay in a Deluxe King room? *(answers no, capacity is 2)*
- Is parking free?
- What is the cancellation policy?
- Is Wi-Fi free?
- Does the hotel have a casino? *(shows the grounded refusal)*
