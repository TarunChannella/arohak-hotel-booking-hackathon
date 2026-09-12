import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOMS = {
    "Deluxe King": (2, 8500, 4),
    "Deluxe Twin": (2, 8500, 4),
    "Premier Sea View": (3, 12500, 3),
    "Executive Suite": (3, 18000, 2),
    "Family Suite": (4, 22000, 2),
}


class BookingStore:
    def __init__(self, path=None):
        self.path = str(path or os.environ.get("HOTEL_DB") or Path(__file__).parent / "data" / "hotel.db")
        self.setup()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def setup(self):
        with self.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY, guest_name TEXT NOT NULL, email TEXT NOT NULL,
                room_type TEXT NOT NULL, guests INTEGER NOT NULL,
                check_in TEXT NOT NULL, check_out TEXT NOT NULL,
                total INTEGER NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL)""")

    @staticmethod
    def _dates(check_in, check_out):
        start, end = date.fromisoformat(check_in), date.fromisoformat(check_out)
        if end <= start:
            raise ValueError("Check-out must be after check-in.")
        if start < date.today():
            raise ValueError("Check-in cannot be in the past.")
        return start, end

    def availability(self, check_in, check_out, guests=1):
        start, end = self._dates(check_in, check_out)
        nights = (end - start).days
        results = []
        with self.connection() as conn:
            for room_type, (capacity, price, inventory) in ROOMS.items():
                if guests > capacity:
                    continue
                used = conn.execute("""SELECT COUNT(*) FROM bookings
                    WHERE room_type=? AND status='CONFIRMED'
                    AND check_in < ? AND check_out > ?""", (room_type, check_out, check_in)).fetchone()[0]
                results.append({"room_type": room_type, "capacity": capacity, "price_per_night": price,
                                "available": max(0, inventory - used), "nights": nights, "total": price * nights})
        return results

    def create(self, payload):
        required = ["guest_name", "email", "room_type", "guests", "check_in", "check_out"]
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValueError("Missing required fields: " + ", ".join(missing))
        room_type = payload["room_type"]
        if room_type not in ROOMS:
            raise ValueError("Unknown room type.")
        guests = int(payload["guests"])
        if guests < 1 or guests > ROOMS[room_type][0]:
            raise ValueError(f"{room_type} allows 1 to {ROOMS[room_type][0]} guests.")
        available = next((r for r in self.availability(payload["check_in"], payload["check_out"], guests) if r["room_type"] == room_type), None)
        if not available or available["available"] < 1:
            raise ValueError("That room is unavailable for the selected dates.")
        booking_id = "MGM-" + uuid.uuid4().hex[:8].upper()
        with self.connection() as conn:
            conn.execute("INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,?,?)", (
                booking_id, payload["guest_name"].strip(), payload["email"].strip().lower(), room_type,
                guests, payload["check_in"], payload["check_out"], available["total"], "CONFIRMED",
                datetime.now(UTC).isoformat(timespec="seconds")))
        return self.get(booking_id)

    def get(self, booking_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        return dict(row) if row else None

    def list(self, email):
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM bookings WHERE email=? ORDER BY created_at DESC", (email.lower(),)).fetchall()
        return [dict(row) for row in rows]

    def cancel(self, booking_id):
        booking = self.get(booking_id)
        if not booking:
            raise LookupError("Booking not found.")
        if booking["status"] == "CANCELLED":
            raise ValueError("This booking is already cancelled.")
        deadline = date.fromisoformat(booking["check_in"]) - timedelta(days=1)
        new_status = "CANCELLED" if date.today() <= deadline else "CANCELLATION_REVIEW"
        with self.connection() as conn:
            conn.execute("UPDATE bookings SET status=? WHERE id=?", (new_status, booking_id))
        return self.get(booking_id)
