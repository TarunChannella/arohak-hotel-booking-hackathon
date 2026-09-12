# PROJECT STATUS — AROHAK Hotel Booking Hackathon

**Last updated:** 2026-09-12 10:40 IST
**Phase:** Sprint 1 — Mandatory MVP implemented and verified
**Requirements source:** [docs/AROHAK_PROBLEM_STATEMENT.md](docs/AROHAK_PROBLEM_STATEMENT.md)

> No passwords, tokens, API keys or secrets are recorded in this file. Demo
> login credentials for the local throwaway database are listed in the README
> because judges need them to sign in; they are overridable via `DEMO_PASSWORD`.

---

## 1. Repository state

| Item | Value |
|---|---|
| Branch | `main` |
| Latest commit | `34bae9e` — Implement Mandatory MVP: auth, roles, hotel/room management, booking |
| Remote | `https://github.com/TarunChannella/arohak-hotel-booking-hackathon.git` |
| Pushed | **No.** Awaiting the Sprint 1 push window (12:45–1:00 PM IST). |

---

## 2. Commands

**Run:** `python server.py` → <http://127.0.0.1:8000>
Overrides: `PORT`, `HOTEL_DB`, `DEMO_PASSWORD`

**Test:** `python -m unittest discover -s tests -v`

---

## 3. Test result

**56 tests, 0 failures, 0 errors — OK** (Python 3.12, Windows 11, ~25s).

| Group | Tests | Covers |
|---|---:|---|
| `AuthTests` | 13 | registration, login, hashing, sessions, expiry, role matrix |
| `HotelTests` | 10 | hotel fields and updates, room CRUD, validation, deactivation |
| `BookingTests` | 13 | search, booking fields, overlap, capacity, isolation, concurrency |
| `CancellationTests` | 14 | deadline, direct cancel, review queue, approve/reject, completion |
| `RagTests` | 6 | grounding, refusal, citations |

The suite takes ~25s because PBKDF2 runs 200,000 rounds per registration. That
cost is deliberate and is what makes the stored hashes worth anything.

---

## 4. Implemented Mandatory MVP requirements

### §1 Authentication and Roles — 15 marks — COMPLETE

- Registration with name, email, password, role; login with email and password.
- PBKDF2-HMAC-SHA256, 200,000 rounds, random 16-byte per-user salt, constant-time
  comparison. Verified by test that no plaintext reaches the database.
- Opaque 256-bit session tokens, server-side, 12-hour expiry, delivered as an
  `HttpOnly` `SameSite=Strict` cookie. Logout deletes the session.
- Three roles enforced **server-side on every route**. The client never declares
  its own role.
- Self-registration cannot grant a staff role; administrators create staff via
  `POST /api/staff`.
- Receptionists hold no admin-level functionality — verified by test and over
  HTTP (403 on hotel edit and room creation).

### §2 Single Hotel and Room Management — 15 marks — COMPLETE

- Hotel record with all eight required fields, seeded from the PDF, editable by
  ADMIN, with ACTIVE/INACTIVE status.
- Individual rooms (not room types) with all eight required fields: room ID,
  number, type, capacity, price, availability status, description, amenities.
  Ten rooms seeded across the five PDF categories.
- Add, view, update, manage availability, deactivate. Duplicate room numbers,
  non-numeric capacity and negative prices are rejected.
- Inactive rooms cannot be booked and are hidden from customers; staff still see
  them.
- Availability accounts for existing bookings.

### §3 Customer Hotel Booking — 20 marks — COMPLETE

- Search by check-in, check-out and guest count.
- Listings show room number, type, price, capacity, description, amenities and
  availability.
- Bookings carry all eleven required fields, including `organization_id`,
  `hotel_id`, `room_id`, `customer_id`, `booking_date` and `total_amount`.
- Overlap prevention, including partial overlaps. Same-day turnover is allowed
  by design (one guest checks out, the next checks in).
- **Simultaneous attempts are safe:** the re-check and insert share one
  `BEGIN IMMEDIATE` transaction. A test races ten threads at a single room and
  asserts exactly one winner.
- Confirmation includes booking ID, hotel, room, customer, dates, guests, total
  and status.
- Direct cancellation until one day before check-in, matching the PDF's
  20/19 September example; later requests go to staff review.

**MVP marks supported: 50 of 50**, each backed by passing tests and by the
manual HTTP verification in section 8.

---

## 5. Beyond the MVP (already present)

Not claimed as complete, but implemented and working:

- **§4 Dashboards (3 marks)** — customer filters (all/upcoming/completed/
  cancelled), staff booking list with text search and status filter, and a
  dedicated cancellation-review queue. `COMPLETED` is derived for past stays and
  persisted by `mark_completed()`.
- **§5 Multi-organization (10 marks)** — *schema only.* `organization_id` is on
  users, hotels and bookings with a seeded default organization, so the
  extension will not require rebuilding the MVP. PRODUCT_ADMIN and
  ORGANIZATION_ADMIN roles, cross-organization isolation and multi-hotel
  browsing are **not** implemented.
- **§7 RAG chatbot (17 marks)** — preserved and extended to 6 tests. Retrieval,
  grounding, citations and refusal all verified.

---

## 6. Missing Mandatory MVP requirements

**None.** All three mandatory sections are implemented and verified.

Known gaps outside the MVP, carried forward:

| Ref | Gap | Impact |
|---|---|---|
| §6 | AI booking chatbot (20 marks) not started | Extension only |
| §5 | Multi-organization behaviour beyond the schema | Extension only |
| RAG | PDF §11 FAQ block is still absent from the knowledge base | Chatbot answers fewer FAQs directly |
| RAG | "Can four guests stay in a Deluxe King room?" still answers about the Family Suite rather than refusing | Wrong answer to a plausible demo question |
| RAG | "What time is check-in?" still leads with the check-out sentence | Cosmetic but visible in a demo |

The three RAG defects are pre-existing, documented, and do not affect MVP marks.
They are the first candidates for Sprint 2.

---

## 7. Current architecture

```
Organization -> Hotel -> Room -> Booking
                          User ->
```

| File | Responsibility |
|---|---|
| `db.py` | Schema, connection handling, seed organization/hotel/rooms |
| `auth.py` | Password hashing, sessions, role authorization helpers |
| `hotel.py` | Hotel and individual room management |
| `booking.py` | Availability, atomic booking, cancellation lifecycle |
| `rag.py` | Grounded extractive retrieval over the hotel PDF |
| `server.py` | Routing, cookie sessions, server-side authorization |
| `static/` | Role-aware single-page UI |

**Decisions**

- Standard library only. No pip install, no API key, no network access.
- Authorization is server-side on every route; UI hiding is cosmetic only.
- A cancellation request holds the room until staff decide, because a rejection
  leaves the booking confirmed.
- Booking statuses: `CONFIRMED`, `CANCELLATION_REQUESTED`, `CANCELLED`,
  `COMPLETED`.
- The chatbot is extractive, never generative, so it cannot hallucinate.
- The UI uses `textContent` exclusively — no `innerHTML` — so guest-supplied
  names cannot inject markup.

---

## 8. Manual verification completed

Live server on port 8098, `HOTEL_DB` pointed outside the repository, then
terminated. Repository left clean; no database or cache file committed.

**Authorization (all confirmed over HTTP)**

- [x] Anonymous → 401 on `/api/bookings` and `POST /api/rooms`
- [x] Anonymous cannot self-register as ADMIN → 400
- [x] CUSTOMER → 403 on room creation, hotel edit, cancellation queue
- [x] RECEPTIONIST → 403 on hotel edit and room creation (no admin rights)
- [x] RECEPTIONIST → 200 editing an existing room
- [x] ADMIN → 201 creating a room, 200 editing the hotel
- [x] ADMIN → 403 creating a booking (customers only)
- [x] Logout invalidates the session → subsequent call 401

**Booking and isolation**

- [x] Availability returns full room detail; 2 rooms fit 4 guests
- [x] Booking created: 3 nights in room 501, total ₹66,000
- [x] Confirmation carries hotel, room, customer, dates, guests, total, status
- [x] Second customer double-booking the same room → 400
- [x] Alice sees 1 booking, Bob sees 0 — customer isolation holds
- [x] Bob reading Alice's booking by ID → 404
- [x] Bob cancelling Alice's booking → 404
- [x] Staff see all bookings; `?q=Alice` filter returns 1

**Cancellation lifecycle**

- [x] Cancel 5 days out → CANCELLED, room released
- [x] Cancel 1 day out → CANCELLED (matches the PDF example)
- [x] Cancel same-day → CANCELLATION_REQUESTED, **room still held**
- [x] Duplicate request → rejected
- [x] CUSTOMER approving own request → 403
- [x] RECEPTIONIST rejects → CONFIRMED, room still held
- [x] RECEPTIONIST approves → CANCELLED, room released

**Other**

- [x] `/`, `/app.js`, `/styles.css` all serve 200 with correct content types
- [x] `node --check static/app.js` — valid
- [x] Path traversal `/../booking.py` → 404
- [x] Grounded chat preserved: "Is parking free?" cites §7 Parking and Transportation

**Not verified**

- [ ] Browser DOM interaction — the API and JS syntax were verified, but tabs,
      forms and rendering were not exercised in a real browser
- [ ] Cross-browser and mobile layout

---

## 9. Next action

Sprint 2 candidates in priority order:

1. §6 AI booking chatbot (20 marks) over a controlled tool layer —
   `search_rooms()`, `check_availability()`, `get_booking()`, `create_booking()`,
   `cancel_booking()`, reusing the existing stores so the AI never touches the
   database directly.
2. Fix the three known RAG defects (Deluxe King capacity answer, check-in
   ranking, missing PDF §11 FAQs).
3. §5 multi-organization behaviour on top of the existing schema.

---

## 10. Changed files this phase

| File | Change |
|---|---|
| `db.py` | New — schema, connections, seed data |
| `auth.py` | New — hashing, sessions, role authorization |
| `hotel.py` | New — hotel and room management |
| `booking.py` | Rewritten — real rooms, atomic booking, cancellation lifecycle |
| `server.py` | Rewritten — auth, role-authorized routes, PUT support |
| `static/index.html` | Rewritten — sign-in gate and role-aware panels |
| `static/app.js` | Rewritten — sessions, role-driven tabs, XSS-safe rendering |
| `static/styles.css` | Rewritten — responsive layout for the new UI |
| `tests/test_app.py` | Expanded — 7 tests to 56 |
| `README.md` | Rewritten — run, demo accounts, roles, API, decisions |
| `docs/AROHAK_PROBLEM_STATEMENT.md` | New — official requirements |
| `PROJECT_STATUS.md` | This file |
| `arohak-hotel-rag/` | Removed — accidental nested duplicate |

---

## 11. Update protocol

1. Update after every completed phase and before every push.
2. Refresh: commit hash, test result, implemented requirements, missing items.
3. Never record passwords, tokens or secrets here.
4. Never commit `*.db`, `__pycache__/` or temporary files.
