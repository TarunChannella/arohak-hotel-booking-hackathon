# PROJECT STATUS — AROHAK Hotel Booking Hackathon

**Last updated:** 2026-09-12 11:25 IST
**Phase:** Sprint 1 — MVP complete, true PDF ingestion implemented, assistant and dashboards complete
**Requirements source:** [docs/AROHAK_PROBLEM_STATEMENT.md](docs/AROHAK_PROBLEM_STATEMENT.md)

> No passwords, tokens, API keys or secrets are recorded in this file. Demo
> login credentials for the local throwaway database are listed in the README
> because judges need them to sign in; they are overridable via `DEMO_PASSWORD`.

---

## 1. Repository state

| Item | Value |
|---|---|
| Branch | `main` |
| Latest commit | see section 11 |
| Remote | `https://github.com/TarunChannella/arohak-hotel-booking-hackathon.git` |
| Pushed | **No.** Awaiting the Sprint 1 push window (12:45–1:00 PM IST). |

---

## 2. Commands

**Install:** `python -m pip install -r requirements.txt` (one dependency: `pypdf`)
**Run:** `python server.py` → <http://127.0.0.1:8000>
Overrides: `PORT`, `HOTEL_DB`, `DEMO_PASSWORD`

**Test:** `python -m unittest discover -s tests -v`

---

## 3. Test result

**120 tests, 0 failures, 0 errors — OK** (Python 3.12, Windows 11, ~26s).
Up from 94 at the previous checkpoint.

| Group | Tests | Covers |
|---|---:|---|
| `AuthTests` | 13 | registration, login, hashing, sessions, expiry, role matrix |
| `HotelTests` | 10 | hotel fields and updates, room CRUD, validation, deactivation |
| `BookingTests` | 13 | search, booking fields, overlap, capacity, isolation, concurrency |
| `CancellationTests` | 14 | deadline, direct cancel, review queue, approve/reject, completion |
| `RagTests` | 6 | grounding, refusal, citations |
| `RagCorrectnessTests` | 13 | the QA-reported defects and their regressions |
| `DashboardTests` | 4 | customer status buckets, staff search and status filter |
| `AssistantExtractionTests` | 6 | guests, dates, location, booking reference parsing |
| `AssistantTests` | 15 | confirmation gating, tool-layer scope, cross-customer isolation |
| `PdfIngestionTests` | 7 | real extraction, chunking, page metadata, cache derivation |
| `PdfGroundedAnswerTests` | 9 | answers traced to PDF evidence with page citations |
| `PdfUploadTests` | 10 | validation, traversal, replacement re-index, hotel isolation |

---

## 4. Section 7 corrected — the PDF is now genuinely the source of truth

**The previous 17-mark claim for §7 was not supportable and has been fixed.**
Until this phase the retriever indexed `data/hotel_knowledge.json`, a
hand-maintained file. The PDF sat in `data/` unused. There was no extraction, no
chunking, no upload and no hotel-to-document association, so the requirement to
answer "strictly using information from an uploaded PDF" was not met.

**What was built**

| Requirement | Implementation |
|---|---|
| PDF extraction dependency | `pypdf` in `requirements.txt`; install with `python -m pip install -r requirements.txt` |
| Ingest the supplied PDF on startup | `ingest.ensure_seed_document()` places `data/AROHAK_Hotel_Information_For_RAG.pdf` for the seeded hotel; `load_index()` extracts and indexes it |
| Extract text from the real PDF | `extract_pages()` via pypdf — 4 pages, ~7,900 characters |
| Traceable chunks | `chunk_pages()` splits at the document's own headings — 15 chunks |
| Page and section metadata | Every chunk carries `section` and `page`; every citation reports both |
| Index built from PDF chunks | `HotelRetriever` indexes `ingest.load_index()` output only |
| No hand-written authoritative file | `data/hotel_knowledge.json` **deleted**; a test asserts `rag.py` never references it |
| Cache is derived, not authoritative | `data/index_cache/<hotel_id>.json` is keyed by the PDF's SHA-256 and rebuilds automatically when deleted or when the PDF changes |
| ADMIN-only upload/replacement | `POST /api/hotel/document` plus a Hotel PDF tab in the admin UI |
| Validation | presence, 10 MB limit, `%PDF-` signature, `.pdf` name, safe basename, extractable text |
| Document ↔ hotel association | `hotel_documents` table keyed by `hotel_id`; per-hotel PDF, cache and index paths |
| Rebuild after replacement | upload re-extracts, re-indexes and calls `retriever.reload()` |
| Selected-hotel isolation | a retriever is constructed for one `hotel_id` and reads only that hotel's document |
| PDF never drives availability | inventory and bookings remain in SQLite; a test deactivates rooms and shows availability changes while chatbot answers do not |
| Grounded refusal and citations kept | unchanged, now with page numbers |

**Evidence from the real document.** Extraction pulls phrases that exist only in
the PDF and in no source file — "Standard check-in time is 2:00 PM",
"MeridianGuest", "Chhatrapati Shivaji Maharaj International Airport",
"Skyline 18". Headings land on their true pages: Hotel Overview p1, Room
Categories p2, Wi-Fi and Connectivity p3. Room capacities are parsed from the
extracted table, not from Python: Deluxe King 2, Premier Sea View 3, Family
Suite 4.

**Two real bugs were found and fixed during this work.** The PDF's bullet glyph
extracts as a control character, which was leaking into answers; it is now
stripped. And `server.py` constructed the retriever before the stores created
the schema, so a fresh clone crashed on startup with "no such table:
hotel_documents" — the initialisation order is corrected and was verified by
starting the server against an empty database directory.

---

## 4. Defects fixed this phase

All three were confirmed in browser QA or by earlier audit, and each now has a
regression test that fails against the old behaviour.

| # | Defect | Fix |
|---|---|---|
| 1 | "What time is check-in?" led with *"Standard check-out is 12:00 PM."* | Root cause was two-part: the `+4` relevance bonus tested for the literal `"check in"` while the question says `"check-in"`, and the check-in **policy section never contained the 2:00 PM sentence at all** — only the Overview did. Added a hyphen-normalising `normalize()`, an explicit check-in/check-out intent that boosts the section asked about and penalises the other, a deterministic handler for "what time is check-in/out", and the standard-time sentence to both policy sections. Now answers `"Standard check-in time is 2:00 PM."` citing `2. Hotel Policies - Check-in`. |
| 2 | "Can four guests stay in a Deluxe King room?" answered *"Family Suite: capacity 4…"*, implying yes | Room capacities are now parsed straight from the room table and a named room type is honoured. Answers `"No. The Deluxe King room has a maximum capacity of 2 guests. The Family Suite accommodates up to 4 guests."` Also handles the affirmative case and counts no room can take. |
| 3 | PDF §11 FAQ block (13 Q&A pairs) absent from the knowledge base | Added as section `11. Frequently Asked Questions`. Sentence selection skips `Q:` markers and strips `A:` prefixes so raw question fragments never reach the guest, and near-duplicate sentences are suppressed so the FAQ does not restate the policy sections verbatim. |

Two further improvements fell out of the same work: "Which room is suitable for
five guests?" now correctly says no category accommodates five rather than
returning unrelated food-and-beverage text, and "What is the cancellation policy?"
now states the governing 24-hour rule instead of only the staff-review clause.

---

## 5. Reported search issue — not a defect

Browser QA reported that Find a Room initially showed no result cards. Verified
against the exact reported case: 2026-09-13 to 2026-09-15 for 2 guests, with
Room 201 already booked 2026-09-13 to 2026-09-16. Search returns **9 rooms**
(202, 203, 204, 301, 302, 401, 402, 501, 502), each carrying room number, type,
price, capacity, description, amenities and availability, with Room 201
correctly excluded. The empty state before the first Search click is the
acceptable initial state. No code change was needed.

---

## 6. Requirement status

### Mandatory MVP — 50 of 50, unchanged and still passing

§1 Authentication and Roles (15), §2 Hotel and Room Management (15), §3 Customer
Booking (20). Detail in the previous checkpoint; all tests still green.

### §4 Booking Management Dashboards — 3 marks — COMPLETE

- Customer: All / Upcoming / Completed / Cancelled filters, with full booking
  detail and status on each card. `COMPLETED` is derived for past stays and
  persisted by `mark_completed()`.
- Staff: free-text search across customer name, email, booking reference and
  room number; status filter across all four statuses; full detail; and the
  permitted actions (cancel on behalf, approve/reject cancellations).
- Verified by 4 tests and over live HTTP.

### §6 AI Chatbot — Booking Management — 20 marks — COMPLETE

- Natural-language intent: search, list upcoming, booking details, cancel,
  confirm, decline, unknown.
- Extraction: location, guest count, check-in and check-out. Handles the
  worked example from the problem statement, ISO dates, "20 September",
  "tomorrow", and "for N nights". Multi-turn slot filling — a bare
  "from Sept 25 to Sept 27" completes an earlier partial request.
- **Real availability only.** Every room named came back from a live
  `search_rooms()` call in that turn. When nothing is free it says so.
  A request for another city is refused rather than invented.
- **Controlled tool layer.** `assistant.py` reaches data only through the seven
  `ToolLayer` methods; there is no database handle and no generic query method.
  A test asserts the public surface is exactly those seven names.
- **Confirmation required** before booking or cancelling. The pending action is
  held server-side keyed by user id, so a client cannot forge a confirmation for
  an action that was never proposed, and one customer's "yes" cannot execute
  another's pending action.
- Scoped per user: the assistant cannot read or cancel another customer's
  booking even when given the reference.

### §7 RAG Chatbot — 17 marks — COMPLETE, now PDF-backed

Text is extracted from the actual supplied PDF, chunked with page and section
metadata, and indexed. Answers quote the extracted text and cite section and
page. Absent information is refused. Admin upload replaces the document and
rebuilds that hotel's index. 45 tests across `RagTests`,
`RagCorrectnessTests`, `PdfIngestionTests`, `PdfGroundedAnswerTests` and
`PdfUploadTests`.

---

## 7. Remaining marks

| Section | Marks | Status |
|---|---:|---|
| §1 Authentication and Roles | 15 | Complete |
| §2 Hotel and Room Management | 15 | Complete |
| §3 Customer Booking | 20 | Complete |
| §4 Booking Dashboards | 3 | Complete |
| §6 AI Booking Chatbot | 20 | Complete |
| §7 RAG Chatbot | 17 | Complete — PDF-backed, evidence in section 4 |
| **§5 Multi-Organization** | **10** | **Not started — schema only** |

**Supported: 90 of 100.** The only outstanding work is §5, deliberately deferred
per instruction until the priorities above were complete.

`organization_id` is already on users, hotels and bookings with a seeded default
organization, so §5 needs the PRODUCT_ADMIN and ORGANIZATION_ADMIN roles,
cross-organization isolation checks, multi-hotel support, and the
organization → hotel → rooms customer flow — not a rebuild.

---

## 8. Current architecture

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
| `ingest.py` | PDF extraction, chunking, validation, per-hotel indexing |
| `rag.py` | Grounded extractive retrieval over the extracted PDF chunks |
| `assistant.py` | Controlled booking assistant and its tool layer |
| `server.py` | Routing, cookie sessions, server-side authorization |
| `static/` | Role-aware single-page UI |

**Decisions**

- Standard library only. No pip install, no API key, no network access.
- Authorization is server-side on every route; UI hiding is cosmetic only.
- A cancellation request holds the room until staff decide.
- The chatbot is extractive, never generative, so it cannot hallucinate.
- The assistant has no database handle and must confirm before acting.
- The UI uses `textContent` exclusively — no `innerHTML`.

---

## 9. Manual and API verification completed

Live server on port 8097, `HOTEL_DB` outside the repository, then terminated.

**Assistant (live HTTP)**

- [x] Anonymous → 401; ADMIN → 403 (customers only)
- [x] "I need a room in Mumbai for 2 people from Sept 20 to Sept 23." → 10 real
      rooms listed, confirmation requested, nothing booked yet
- [x] "yes" → booked MGM-… Room 201, ₹25,500 — matches the ₹8,500 × 3 figure
      confirmed in browser QA
- [x] "show my upcoming bookings" → 1 upcoming booking
- [x] "cancel my booking" → explains the deadline, requests confirmation
- [x] "yes" → cancelled and room released
- [x] Another city → refused, not invented
- [x] Fully booked window → "no rooms", no confirmation offered

**Dashboards (live HTTP)**

- [x] Staff status filter: CONFIRMED 0 / CANCELLED 1 after the flow above
- [x] Staff free-text search by customer name → 1 result

**Chatbot (live HTTP)**

- [x] "What time is check-in?" → "Standard check-in time is 2:00 PM."
- [x] "Can four guests stay in a Deluxe King room?" → "No. The Deluxe King room
      has a maximum capacity of 2 guests. …"
- [x] Citations present on every grounded answer

**Search (reproduced directly)**

- [x] 2026-09-13 → 2026-09-15, 2 guests, Room 201 booked: 9 rooms returned with
      all required fields, 201 excluded

**Other**

- [x] `/`, `/app.js`, `/styles.css` serve 200
- [x] `node --check static/app.js` — valid
- [x] Full suite 94/94 green

**Not verified**

- [ ] Browser DOM interaction for the new Booking assistant tab — the endpoint
      and the JS syntax are verified, but the panel has not been clicked through
      in a real browser
- [ ] Cross-browser and mobile layout

---

## 10. Next action

§5 Multi-Organization (10 marks), the only remaining section:

1. `PRODUCT_ADMIN` and `ORGANIZATION_ADMIN` roles.
2. Organization CRUD for PRODUCT_ADMIN.
3. Scope every query by the acting user's organization; add isolation tests.
4. Multiple hotels per organization; receptionists assigned to hotels.
5. Customer flow: organization → hotel → rooms → availability → book.

---

## 11. Changed files this phase (PDF ingestion)

| File | Change |
|---|---|
| `ingest.py` | New — extraction, chunking, validation, per-hotel index and cache |
| `requirements.txt` | New — `pypdf>=4.0` |
| `rag.py` | Indexes PDF chunks instead of a JSON file; citations carry page numbers; global stemming; position tiebreak |
| `db.py` | `hotel_documents` table |
| `server.py` | `GET`/`POST /api/hotel/document`; fixed startup initialisation order |
| `static/index.html` | Admin Hotel PDF panel |
| `static/app.js` | Upload handler, document metadata, page numbers in citations |
| `tests/test_app.py` | 94 → 120 tests; ingestion isolated per test via `HOTEL_DB` |
| `data/hotel_knowledge.json` | **Deleted** — no longer authoritative |
| `.gitignore` | `data/documents/`, `data/index_cache/`, `data/hotel_knowledge.json` |
| `README.md` | Install step, PDF ingestion documentation, endpoints |

---

## 12. Previous phase changed files

| File | Change |
|---|---|
| `assistant.py` | New — controlled booking assistant and tool layer |
| `rag.py` | Hyphen normalisation, check-in/out intent, room-capacity handler, FAQ-aware sentence selection, duplicate suppression |
| `data/hotel_knowledge.json` | Standard check-in/out times added to their policy sections; PDF §11 FAQ section added (12 → 13 sections) |
| `server.py` | `/api/assistant` route, CUSTOMER-only |
| `static/index.html` | Booking assistant panel |
| `static/app.js` | Assistant tab, handler and rendering |
| `tests/test_app.py` | 56 → 94 tests |
| `README.md` | Assistant documentation, endpoint, examples, test count |
| `PROJECT_STATUS.md` | This file |

---

## 13. Update protocol

1. Update after every completed phase and before every push.
2. Refresh: commit hash, test result, implemented requirements, missing items.
3. Never record passwords, tokens or secrets here.
4. Never commit `*.db`, `__pycache__/` or temporary files.
