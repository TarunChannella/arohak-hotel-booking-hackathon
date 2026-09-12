# Meridian Grand Hotel Assistant

A dependency-light hotel RAG chatbot and live booking demo for the AROHAK hackathon.

## What it demonstrates

- Retrieves relevant hotel passages before answering.
- Returns traceable section citations with every grounded answer.
- Refuses to invent information absent from the supplied document.
- Keeps room availability and booking actions in live SQLite data, separate from the PDF knowledge source.
- Supports availability search, booking creation, booking listing, and policy-aware cancellation.

## Run

Requires Python 3.10+.

```bash
python server.py
```

Open <http://127.0.0.1:8000>.

The database is created automatically at `data/hotel.db`. To use a different database:

```bash
HOTEL_DB=/path/to/hotel.db python server.py
```

## Verify

```bash
python -m unittest discover -s tests -v
```

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/chat` | Grounded hotel Q&A |
| GET | `/api/availability` | Live room availability |
| POST | `/api/bookings` | Create a booking |
| GET | `/api/bookings?email=...` | List bookings |
| POST | `/api/bookings/{id}/cancel` | Cancel or request cancellation review |

## Architecture

The application deliberately separates two sources:

1. `data/hotel_knowledge.json` contains sectioned passages derived from the supplied PDF and is used only for policy/facility/room questions.
2. SQLite contains room inventory and bookings and is the only source for availability and booking actions.

The retriever uses weighted token overlap with phrase and title bonuses. The response layer chooses only sentences from retrieved passages, attaches citations, and uses an explicit unavailable response when evidence is insufficient. This makes the demo deterministic, inspectable, and resistant to hallucination without requiring an external API key.

## Demo questions

- What time is check-in?
- Can four guests stay in a Deluxe King room?
- What happens if I check out at 4 PM?
- Is parking free and can I reserve a space?
- Does the hotel have a casino? *(shows the grounded refusal)*

