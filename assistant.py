"""Controlled AI booking assistant (problem statement section 6).

The assistant never touches the database. It may only call the small set of
service tools defined in ToolLayer below, each of which is scoped to the signed-in
user, so the assistant cannot read or change another customer's data even if the
language model is steered into trying.

Availability is never invented: every room the assistant mentions came back from
a live search_rooms() call in this turn.

Booking and cancellation always require a second, explicit confirmation. The
pending action is held server-side, keyed by user id, so the client cannot forge
a confirmation for an action the assistant never proposed.
"""
import re
from datetime import date, datetime, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
AFFIRMATIVE = {"yes", "y", "yeah", "yep", "confirm", "confirmed", "ok", "okay",
               "sure", "please do", "go ahead", "do it", "book it", "proceed"}
NEGATIVE = {"no", "n", "nope", "cancel that", "stop", "never mind", "nevermind", "abort"}


class ToolLayer:
    """The only way the assistant can reach application data.

    Every method takes the acting user and filters by it. There is no generic
    query method and no database handle exposed here on purpose.
    """

    def __init__(self, bookings, hotels):
        self._bookings = bookings
        self._hotels = hotels

    def get_hotel(self, hotel_id=None):
        hotel = self._hotels.get_hotel(hotel_id) if hotel_id else self._hotels.get_hotel()
        if not hotel:
            return None
        return {"id": hotel["id"], "name": hotel["name"], "city": hotel["city"],
                "address": hotel["address"], "status": hotel["status"]}

    def search_rooms(self, check_in, check_out, guests, hotel_id=None):
        if hotel_id:
            return self._bookings.search(check_in, check_out, guests, hotel_id=hotel_id)
        return self._bookings.search(check_in, check_out, guests)

    def check_availability(self, room_id, check_in, check_out):
        return self._bookings.is_available(room_id, check_in, check_out)

    def get_booking(self, user, booking_id):
        booking = self._bookings.get(booking_id)
        if not booking or booking["customer_id"] != user["id"]:
            return None
        return booking

    def list_bookings(self, user, upcoming_only=False):
        rows = self._bookings.list_for_customer(user["id"])
        if upcoming_only:
            rows = [b for b in rows if b["display_status"] in ("CONFIRMED", "CANCELLATION_REQUESTED")
                    and not b["is_past"]]
        return rows

    def create_booking(self, user, room_id, check_in, check_out, guests):
        return self._bookings.create(user, {"room_id": room_id, "check_in": check_in,
                                            "check_out": check_out, "guests": guests})

    def cancel_booking(self, user, booking_id):
        return self._bookings.cancel(booking_id, user["id"])


# ---------------------------------------------------------------- extraction

def extract_guests(text):
    lowered = text.lower()
    match = re.search(r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s*(?:people|person|persons|guests?|adults?|pax)\b", lowered)
    if not match:
        match = re.search(r"\bfor\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\b", lowered)
    if not match:
        return None
    token = match.group(1)
    value = NUMBER_WORDS.get(token, None) or (int(token) if token.isdigit() else None)
    return value if value and 1 <= value <= 20 else None


def _make_date(year, month, day, today):
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    # A bare "Sept 20" that has already passed means next year.
    if candidate < today and year == today.year:
        try:
            candidate = date(year + 1, month, day)
        except ValueError:
            return None
    return candidate


def extract_dates(text, today=None):
    """Pull a check-in and check-out date out of natural language.

    Handles ISO dates, "Sept 20 to Sept 23", "20 September", "tonight",
    "tomorrow", and "for N nights".
    """
    today = today or date.today()
    lowered = text.lower()
    found = []

    for iso in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", lowered):
        made = _make_date(int(iso[0]), int(iso[1]), int(iso[2]), today)
        if made:
            found.append(made)

    if not found:
        pattern = r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b"
        for month, day in re.findall(pattern, lowered):
            made = _make_date(today.year, MONTHS[month], int(day), today)
            if made:
                found.append(made)
        for day, month in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", lowered):
            made = _make_date(today.year, MONTHS[month], int(day), today)
            if made and made not in found:
                found.append(made)

    if not found:
        if "tonight" in lowered or "today" in lowered:
            found.append(today)
        elif "tomorrow" in lowered:
            found.append(today + timedelta(days=1))
        elif "next week" in lowered:
            found.append(today + timedelta(days=7))

    found = sorted(set(found))
    if not found:
        return None, None
    check_in = found[0]
    if len(found) > 1:
        return check_in, found[1]

    nights = re.search(r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s*nights?\b", lowered)
    if nights:
        token = nights.group(1)
        count = NUMBER_WORDS.get(token) or int(token)
        return check_in, check_in + timedelta(days=max(1, count))
    return check_in, None


def extract_location(text, known_city):
    if known_city and known_city.lower() in text.lower():
        return known_city
    for match in re.finditer(r"\b(?:in|at|near|to)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)", text):
        candidate = match.group(1).strip()
        first = candidate.split()[0].lower().rstrip(".")
        # "to Sept 25" is a date, not a destination.
        if first[:3] in MONTHS:
            continue
        return candidate
    return None


def extract_booking_id(text):
    match = re.search(r"\b(MGM-[A-Za-z0-9]{4,})\b", text.upper())
    return match.group(1) if match else None


def classify(text):
    lowered = text.lower().strip()
    if any(lowered == word or lowered.startswith(word + " ") for word in NEGATIVE) or lowered in NEGATIVE:
        return "decline"
    if lowered in AFFIRMATIVE or any(lowered.startswith(word) for word in AFFIRMATIVE):
        return "confirm"
    if re.search(r"\bcancel\b", lowered):
        return "cancel"
    if re.search(r"\b(upcoming|my bookings|my reservation|what have i booked|show my)\b", lowered):
        return "list"
    if extract_booking_id(text) and re.search(r"\b(detail|status|about|show|look up|find)\b", lowered):
        return "details"
    if re.search(r"\b(book|reserve|room|stay|availability|available|night)\b", lowered):
        return "search"
    if extract_booking_id(text):
        return "details"
    return "unknown"


# ---------------------------------------------------------------- assistant

class BookingAssistant:
    """Turns natural language into calls on the controlled tool layer."""

    def __init__(self, tools):
        self.tools = tools
        self._pending = {}   # user id -> the action awaiting confirmation
        self._context = {}   # user id -> last extracted search criteria
        self._hotel = {}     # user id -> the hotel currently selected

    # -- helpers -------------------------------------------------

    @staticmethod
    def _reply(text, **extra):
        payload = {"reply": text, "requires_confirmation": False, "action": None}
        payload.update(extra)
        return payload

    @staticmethod
    def _describe(booking):
        return (f"{booking['id']} — Room {booking['room_number']} ({booking['room_type']}) "
                f"at {booking['hotel_name']}, {booking['check_in']} to {booking['check_out']}, "
                f"{booking['guests']} guest(s), ₹{booking['total_amount']:,}, "
                f"{booking['display_status'].replace('_', ' ').title()}")

    def pending_for(self, user):
        return self._pending.get(user["id"])

    def clear(self, user):
        self._pending.pop(user["id"], None)
        self._context.pop(user["id"], None)

    # -- entry point ---------------------------------------------

    def respond(self, user, message, hotel_id=None):
        """Answer one turn. hotel_id selects which hotel the customer is booking."""
        message = (message or "").strip()
        self._hotel[user["id"]] = hotel_id or self._hotel.get(user["id"])
        if not message:
            return self._reply("Tell me what you need — for example, "
                               "\"I need a room in Mumbai for 2 people from Sept 20 to Sept 23.\"")

        kind = classify(message)
        pending = self._pending.get(user["id"])

        if pending and kind == "confirm":
            return self._execute(user, pending)
        if pending and kind == "decline":
            self._pending.pop(user["id"], None)
            return self._reply("No problem, I have not made any changes. Anything else?")
        if kind == "confirm":
            return self._reply("There is nothing waiting for confirmation. "
                               "Tell me the dates and number of guests and I will check availability.")

        if kind == "list":
            return self._list(user)
        if kind == "details":
            return self._details(user, message)
        if kind == "cancel":
            return self._propose_cancel(user, message)
        if kind == "search":
            return self._search(user, message)
        # A bare follow-up like "from Sept 25 to Sept 27" continues the search
        # we already started rather than starting over.
        if self._context.get(user["id"]) and (extract_dates(message)[0] or extract_guests(message)):
            return self._search(user, message)
        return self._reply(
            "I can search for rooms, show your bookings, explain a booking, or cancel one. "
            "For example: \"I need a room in Mumbai for 2 people from Sept 20 to Sept 23.\"")

    # -- intents -------------------------------------------------

    def _list(self, user):
        rows = self.tools.list_bookings(user, upcoming_only=True)
        if not rows:
            return self._reply("You have no upcoming bookings.")
        lines = "\n".join("• " + self._describe(b) for b in rows)
        return self._reply(f"You have {len(rows)} upcoming booking(s):\n{lines}", data={"bookings": rows})

    def _details(self, user, message):
        booking_id = extract_booking_id(message)
        if not booking_id:
            return self._reply("Which booking reference should I look up? It looks like MGM-XXXXXXXX.")
        booking = self.tools.get_booking(user, booking_id)
        if not booking:
            return self._reply(f"I could not find booking {booking_id} on your account.")
        return self._reply(self._describe(booking), data={"booking": booking})

    def _search(self, user, message):
        hotel = self.tools.get_hotel(self._hotel.get(user["id"]))
        if not hotel:
            return self._reply("Please choose a hotel before I search for rooms.")
        location = extract_location(message, hotel["city"])
        guests = extract_guests(message)
        check_in, check_out = extract_dates(message)
        context = self._context.get(user["id"], {})
        guests = guests or context.get("guests")
        check_in = check_in or context.get("check_in")
        check_out = check_out or context.get("check_out")

        if location and location.lower() not in (hotel["city"].lower(), hotel["name"].lower()):
            return self._reply(
                f"I can only book {hotel['name']} in {hotel['city']}, so I have nothing in {location}.")

        missing = []
        if not check_in:
            missing.append("the check-in date")
        if not check_out:
            missing.append("the check-out date")
        if not guests:
            missing.append("how many guests")
        if missing:
            self._context[user["id"]] = {"guests": guests, "check_in": check_in, "check_out": check_out}
            return self._reply("I still need " + " and ".join(missing) + ".")

        check_in_s = check_in.isoformat() if hasattr(check_in, "isoformat") else str(check_in)
        check_out_s = check_out.isoformat() if hasattr(check_out, "isoformat") else str(check_out)
        try:
            rooms = self.tools.search_rooms(check_in_s, check_out_s, guests, hotel_id=hotel["id"])
        except ValueError as exc:
            return self._reply(str(exc))

        self._context[user["id"]] = {"guests": guests, "check_in": check_in, "check_out": check_out}
        if not rooms:
            return self._reply(
                f"I have no rooms for {guests} guest(s) at {hotel['name']} from "
                f"{check_in_s} to {check_out_s}. Try different dates and I will check again.")

        best = rooms[0]
        lines = "\n".join(
            f"• Room {r['room_number']} ({r['room_type']}), sleeps {r['capacity']}, "
            f"₹{r['price_per_night']:,} per night — ₹{r['total_amount']:,} total"
            for r in rooms[:5])
        self._pending[user["id"]] = {
            "type": "create", "room_id": best["room_id"], "room_number": best["room_number"],
            "room_type": best["room_type"], "check_in": check_in_s, "check_out": check_out_s,
            "guests": guests, "total_amount": best["total_amount"]}
        return self._reply(
            f"{len(rooms)} room(s) are available at {hotel['name']} from {check_in_s} to "
            f"{check_out_s} for {guests} guest(s):\n{lines}\n\n"
            f"Shall I book Room {best['room_number']} ({best['room_type']}) for "
            f"₹{best['total_amount']:,}? Reply yes to confirm.",
            requires_confirmation=True, action="create", data={"rooms": rooms})

    def _propose_cancel(self, user, message):
        booking_id = extract_booking_id(message)
        if not booking_id:
            upcoming = self.tools.list_bookings(user, upcoming_only=True)
            if not upcoming:
                return self._reply("You have no upcoming bookings to cancel.")
            if len(upcoming) > 1:
                lines = "\n".join("• " + self._describe(b) for b in upcoming)
                return self._reply("Which booking should I cancel? Give me the reference.\n" + lines,
                                   data={"bookings": upcoming})
            booking_id = upcoming[0]["id"]

        booking = self.tools.get_booking(user, booking_id)
        if not booking:
            return self._reply(f"I could not find booking {booking_id} on your account.")
        if booking["status"] in ("CANCELLED", "COMPLETED"):
            return self._reply(f"Booking {booking['id']} is already "
                               f"{booking['display_status'].replace('_', ' ').lower()}.")
        if booking["status"] == "CANCELLATION_REQUESTED":
            return self._reply(f"Booking {booking['id']} already has a cancellation request awaiting staff review.")

        self._pending[user["id"]] = {"type": "cancel", "booking_id": booking["id"]}
        if booking["can_cancel_directly"]:
            note = "This is before the deadline, so it will be cancelled immediately."
        else:
            note = ("This is past the 24-hour deadline, so it becomes a cancellation request "
                    "for staff review and the room stays held until they decide.")
        return self._reply(f"{self._describe(booking)}\n\n{note} Shall I proceed? Reply yes to confirm.",
                           requires_confirmation=True, action="cancel")

    # -- confirmed actions ---------------------------------------

    def _execute(self, user, pending):
        self._pending.pop(user["id"], None)
        try:
            if pending["type"] == "create":
                booking = self.tools.create_booking(
                    user, pending["room_id"], pending["check_in"], pending["check_out"], pending["guests"])
                self._context.pop(user["id"], None)
                return self._reply("Booked. " + self._describe(booking),
                                   action="created", data={"booking": booking})
            booking = self.tools.cancel_booking(user, pending["booking_id"])
            if booking["status"] == "CANCELLED":
                text = f"Booking {booking['id']} is cancelled and the room has been released."
            else:
                text = (f"Cancellation request submitted for booking {booking['id']}. "
                        "Staff will review it, and the booking stays confirmed until they decide.")
            return self._reply(text, action="cancelled", data={"booking": booking})
        except (ValueError, LookupError) as exc:
            return self._reply(f"I could not complete that: {exc}")
