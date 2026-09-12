# PROJECT STATUS — AROHAK Hotel Booking Hackathon

**Last updated:** 2026-09-12 10:30 IST
**Phase:** Sprint 1 baseline — preparation commits complete, Mandatory MVP not yet started
**Requirements source:** [docs/AROHAK_PROBLEM_STATEMENT.md](docs/AROHAK_PROBLEM_STATEMENT.md)

> No passwords, tokens, API keys or secrets are recorded in this file.

---

## 1. Repository state

| Item | Value |
|---|---|
| Branch | `main` |
| Latest commit | `259112c` — Remove accidental nested project copy |
| Remote | `https://github.com/TarunChannella/arohak-hotel-booking-hackathon.git` |
| Pushed | **No.** Awaiting the Sprint 1 push window (12:45–1:00 PM IST). |

---

## 2. Commands

**Run:** `python server.py` → <http://127.0.0.1:8000>
Overrides: `PORT=8099`, `HOTEL_DB=/path/to/hotel.db`

**Test:** `python -m unittest discover -s tests -v`

---

## 3. Current test result

**7 tests passing**, 0 failures, 0 errors (Python 3.12, Windows 11).
4 RAG tests, 3 booking tests. No third-party packages.

---

## 4. Currently implemented starter functionality

- Grounded PDF RAG chatbot: TF-IDF retrieval over 12 sections, extractive
  answers, section citations, explicit refusal when evidence is absent.
- Anonymous booking demo: availability by date range, booking creation,
  booking list by email, cancellation with a 24-hour direct-cancel deadline.
- Room *types* hardcoded in a Python dict with invented inventory counts.
- Single-page UI with three tabs (chat / find a room / my bookings).
- Static file server with a path-traversal guard.

---

## 5. Missing Mandatory MVP requirements (all 50 marks at risk)

The starter predates the official problem statement. It has **no users, no
authentication, no roles, no hotel entity and no individual room entity**.
Against the official MVP:

### §1 Authentication and Roles — 15 marks — NOT IMPLEMENTED
- No user table, registration, login or session mechanism.
- No ADMIN / RECEPTIONIST / CUSTOMER roles; every endpoint is anonymous.
- No server-side authorization on any route.

### §2 Single Hotel and Room Management — 15 marks — NOT IMPLEMENTED
- No hotel record (no name, address, city, description, contact, email, status).
- No individual rooms. `ROOMS` in `booking.py` is a dict of five *types* with
  hardcoded counts; there are no room numbers, descriptions, amenities or
  per-room availability status.
- No staff CRUD for rooms; no deactivation; inactive rooms are not a concept.

### §3 Customer Hotel Booking — 20 marks — PARTIAL (~6 of 20)
Working: date+guest search, overlap prevention for confirmed bookings, total
amount, 24-hour direct-cancel rule matching the PDF's 20/19 September example,
late requests routed to review.
Missing:
- Bookings are keyed by a free-text email, not a customer ID; anyone can list
  anyone's bookings by typing their address.
- No `booking_id`-linked customer/organization/hotel/room identifiers.
- Listings lack room number, description and amenities.
- Simultaneous booking attempts are **not** safe — availability check and insert
  are separate transactions (TOCTOU race).
- No staff approve/reject step, so `CANCELLATION_REVIEW` is a dead end.

**Honest current MVP score: approximately 6 of 50.**

---

## 6. Current architecture

- **Stdlib only.** `http.server.ThreadingHTTPServer`, `sqlite3`, `json`, `re`.
  No pip install, no API key, no network access.
- **Two separated sources of truth:** `data/hotel_knowledge.json` answers policy
  questions; SQLite owns bookings. The PDF is never consulted for availability.
- **Extractive RAG, never generative** — replies are sentences copied verbatim
  from retrieved passages, which makes hallucination structurally impossible.
- **Single `bookings` table.** No users, hotels, rooms or organizations yet.

---

## 7. Marks currently supported

Only where demonstrably complete and verified:

| Section | Marks | Supported | Basis |
|---|---|---|---|
| §1 Auth and Roles | 15 | **0** | Nothing implemented |
| §2 Hotel and Room Management | 15 | **0** | Nothing implemented |
| §3 Customer Booking | 20 | **~6** | Search, overlap, totals, cancellation deadline |
| §7 RAG Chatbot (AI extension) | 17 | substantially complete | Retrieval, grounding, citations, refusal all verified |

§4, §5 and §6 are not started. §7 is an extension, not MVP, and does not offset
the MVP gap.

---

## 8. Next action

**Implement the complete 50-mark Mandatory MVP**, in this order:

1. Database schema and secure authentication (hashed passwords, sessions)
2. Server-side role authorization
3. Hotel management
4. Individual room management
5. Customer availability search
6. Atomic booking creation with overlap protection
7. Booking confirmation and customer booking list
8. Direct cancellation and late cancellation request
9. Staff cancellation approval/rejection
10. Role-aware responsive UI
11. Mandatory tests
12. README and status update

`organization_id` and a seeded default organization are included in the schema
from the start so the §5 multi-organization extension will not require
rebuilding the MVP. The existing grounded PDF chatbot is preserved.

---

## 9. Update protocol

1. Update after every completed phase and before every push.
2. Refresh: commit hash, test result, implemented requirements, missing items.
3. Never record passwords, tokens or secrets here.
4. Never commit `*.db`, `__pycache__/` or temporary files.
