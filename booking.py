"""Availability search, booking creation and the cancellation lifecycle.

Booking statuses: CONFIRMED, CANCELLATION_REQUESTED, CANCELLED, COMPLETED.

A room is considered occupied by any booking that is CONFIRMED or
CANCELLATION_REQUESTED. A cancellation request does NOT release the room,
because staff may reject it and the booking then stays confirmed.
"""
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta

import db

HOLDING_STATUSES = ("CONFIRMED", "CANCELLATION_REQUESTED")
# Overlap test: an existing booking blocks a new one when it starts before the
# new checkout and ends after the new checkin. Same-day turnover is allowed.
OVERLAP_SQL = """
    SELECT 1 FROM bookings
    WHERE room_id=? AND status IN ('CONFIRMED','CANCELLATION_REQUESTED')
      AND check_in < ? AND check_out > ?
"""


class BookingStore:
    def __init__(self, path=None):
        self.path = db.database_path(path)
        db.initialize(self.path)

    # ---------- dates ----------

    @staticmethod
    def parse_dates(check_in, check_out):
        try:
            start = date.fromisoformat(str(check_in))
            end = date.fromisoformat(str(check_out))
        except ValueError:
            raise ValueError("Dates must be in YYYY-MM-DD format.")
        if end <= start:
            raise ValueError("Check-out must be after check-in.")
        if start < date.today():
            raise ValueError("Check-in cannot be in the past.")
        return start, end

    # ---------- availability ----------

    def search(self, check_in, check_out, guests=1, hotel_id=db.DEFAULT_HOTEL_ID):
        """Rooms that are active, large enough, and free for the whole range."""
        start, end = self.parse_dates(check_in, check_out)
        guests = int(guests or 1)
        if guests < 1:
            raise ValueError("Number of guests must be at least 1.")
        nights = (end - start).days
        results = []
        with db.connect(self.path) as conn:
            rooms = conn.execute(
                """SELECT * FROM rooms
                   WHERE hotel_id=? AND availability_status='ACTIVE' AND capacity>=?
                   ORDER BY price_per_night, CAST(room_number AS INTEGER)""",
                (hotel_id, guests),
            ).fetchall()
            for room in rooms:
                taken = conn.execute(OVERLAP_SQL, (room["id"], check_out, check_in)).fetchone()
                if taken:
                    continue
                results.append({
                    "room_id": room["id"], "room_number": room["room_number"],
                    "room_type": room["room_type"], "capacity": room["capacity"],
                    "price_per_night": room["price_per_night"],
                    "description": room["description"], "amenities": room["amenities"],
                    "availability_status": room["availability_status"],
                    "available": True, "nights": nights,
                    "total_amount": room["price_per_night"] * nights,
                })
        return results

    def is_available(self, room_id, check_in, check_out):
        with db.connect(self.path) as conn:
            return conn.execute(OVERLAP_SQL, (room_id, check_out, check_in)).fetchone() is None

    # ---------- creation ----------

    def create(self, customer, payload):
        """Create a booking atomically.

        The availability re-check and the INSERT run inside a single
        BEGIN IMMEDIATE transaction, so two simultaneous attempts on the last
        free room cannot both succeed: the second one serialises behind the
        first, sees the new row, and is rejected.
        """
        room_id = str(payload.get("room_id", "")).strip()
        if not room_id:
            raise ValueError("Room selection is required.")
        start, end = self.parse_dates(payload.get("check_in"), payload.get("check_out"))
        check_in, check_out = start.isoformat(), end.isoformat()
        nights = (end - start).days
        try:
            guests = int(payload.get("guests", 0))
        except (TypeError, ValueError):
            raise ValueError("Number of guests must be a whole number.")
        if guests < 1:
            raise ValueError("Number of guests must be at least 1.")

        booking_id = "MGM-" + uuid.uuid4().hex[:8].upper()
        with db.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
            if not room:
                raise LookupError("Room not found.")
            if room["availability_status"] != "ACTIVE":
                raise ValueError("This room is not available for booking.")
            hotel = conn.execute("SELECT * FROM hotels WHERE id=?", (room["hotel_id"],)).fetchone()
            if not hotel or hotel["status"] != "ACTIVE":
                raise ValueError("This hotel is not currently accepting bookings.")
            if guests > room["capacity"]:
                raise ValueError(
                    f"Room {room['room_number']} ({room['room_type']}) has a maximum capacity of {room['capacity']} guests."
                )
            if conn.execute(OVERLAP_SQL, (room_id, check_out, check_in)).fetchone():
                raise ValueError("That room is already booked for the selected dates.")
            total = room["price_per_night"] * nights
            conn.execute(
                "INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (booking_id, customer["id"], hotel["organization_id"], hotel["id"], room_id,
                 check_in, check_out, guests, datetime.now(UTC).isoformat(timespec="seconds"),
                 total, "CONFIRMED"),
            )
        return self.get(booking_id)

    # ---------- reads ----------

    def get(self, booking_id):
        with db.connect(self.path) as conn:
            row = conn.execute(
                """SELECT b.*, r.room_number, r.room_type, r.capacity, r.price_per_night,
                          r.description AS room_description, r.amenities,
                          h.name AS hotel_name, h.city AS hotel_city, h.address AS hotel_address,
                          u.name AS customer_name, u.email AS customer_email
                   FROM bookings b
                   JOIN rooms r ON r.id = b.room_id
                   JOIN hotels h ON h.id = b.hotel_id
                   JOIN users u ON u.id = b.customer_id
                   WHERE b.id=?""",
                (booking_id,),
            ).fetchone()
        return self._decorate(dict(row)) if row else None

    def list_for_customer(self, customer_id):
        return self._list("WHERE b.customer_id=?", (customer_id,))

    def list_all(self, status=None, query=None, hotel_ids=None):
        """Staff listing. hotel_ids=None means no hotel restriction."""
        clauses, params = [], []
        if hotel_ids is not None:
            if not hotel_ids:
                return []
            clauses.append("b.hotel_id IN (" + ",".join("?" * len(hotel_ids)) + ")")
            params.extend(hotel_ids)
        if status:
            clauses.append("b.status=?")
            params.append(status)
        if query:
            clauses.append("(u.name LIKE ? OR u.email LIKE ? OR b.id LIKE ? OR r.room_number LIKE ?)")
            params.extend([f"%{query}%"] * 4)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._list(where, tuple(params))

    def _list(self, where, params):
        with db.connect(self.path) as conn:
            rows = conn.execute(
                f"""SELECT b.*, r.room_number, r.room_type, r.capacity, r.price_per_night,
                           r.description AS room_description, r.amenities,
                           h.name AS hotel_name, h.city AS hotel_city, h.address AS hotel_address,
                           u.name AS customer_name, u.email AS customer_email
                    FROM bookings b
                    JOIN rooms r ON r.id = b.room_id
                    JOIN hotels h ON h.id = b.hotel_id
                    JOIN users u ON u.id = b.customer_id
                    {where}
                    ORDER BY b.check_in DESC, b.booking_date DESC""",
                params,
            ).fetchall()
        return [self._decorate(dict(r)) for r in rows]

    @staticmethod
    def _decorate(booking):
        """Add derived fields the dashboards need."""
        checkout = date.fromisoformat(booking["check_out"])
        booking["is_past"] = checkout < date.today()
        if booking["status"] == "CONFIRMED" and booking["is_past"]:
            booking["display_status"] = "COMPLETED"
        else:
            booking["display_status"] = booking["status"]
        booking["can_cancel_directly"] = (
            booking["status"] == "CONFIRMED"
            and date.today() <= date.fromisoformat(booking["check_in"]) - timedelta(days=1)
        )
        return booking

    # ---------- cancellation ----------

    @staticmethod
    def direct_cancellation_deadline(check_in):
        """Last date on which a customer may cancel directly.

        The PDF's worked example: for a 20 September check-in, direct
        cancellation is available until 19 September.
        """
        return date.fromisoformat(check_in) - timedelta(days=1)

    def cancel(self, booking_id, customer_id=None):
        """Customer-initiated cancellation.

        Before the deadline the booking is cancelled outright. After it, the
        booking moves to CANCELLATION_REQUESTED for staff review and keeps
        holding the room.
        """
        booking = self.get(booking_id)
        if not booking:
            raise LookupError("Booking not found.")
        if customer_id is not None and booking["customer_id"] != customer_id:
            raise LookupError("Booking not found.")
        if booking["status"] == "CANCELLED":
            raise ValueError("This booking is already cancelled.")
        if booking["status"] == "CANCELLATION_REQUESTED":
            raise ValueError("A cancellation request for this booking is already awaiting staff review.")
        if booking["status"] == "COMPLETED" or booking["is_past"]:
            raise ValueError("A completed stay cannot be cancelled.")

        deadline = self.direct_cancellation_deadline(booking["check_in"])
        new_status = "CANCELLED" if date.today() <= deadline else "CANCELLATION_REQUESTED"
        with db.connect(self.path) as conn:
            conn.execute("UPDATE bookings SET status=? WHERE id=?", (new_status, booking_id))
        return self.get(booking_id)

    def list_cancellation_requests(self, hotel_ids=None):
        if hotel_ids is None:
            return self._list("WHERE b.status='CANCELLATION_REQUESTED'", ())
        if not hotel_ids:
            return []
        placeholders = ",".join("?" * len(hotel_ids))
        return self._list(
            f"WHERE b.status='CANCELLATION_REQUESTED' AND b.hotel_id IN ({placeholders})",
            tuple(hotel_ids))

    def review_cancellation(self, booking_id, approve):
        """Staff decision on a late cancellation request.

        Approved  -> CANCELLED (the room is released).
        Rejected  -> CONFIRMED (the booking stays active, per the PDF).
        """
        booking = self.get(booking_id)
        if not booking:
            raise LookupError("Booking not found.")
        if booking["status"] != "CANCELLATION_REQUESTED":
            raise ValueError("This booking has no cancellation request awaiting review.")
        new_status = "CANCELLED" if approve else "CONFIRMED"
        with db.connect(self.path) as conn:
            conn.execute("UPDATE bookings SET status=? WHERE id=?", (new_status, booking_id))
        return self.get(booking_id)

    def mark_completed(self):
        """Move past confirmed stays to COMPLETED. Safe to call repeatedly."""
        with db.connect(self.path) as conn:
            conn.execute(
                "UPDATE bookings SET status='COMPLETED' WHERE status='CONFIRMED' AND check_out < ?",
                (date.today().isoformat(),),
            )
