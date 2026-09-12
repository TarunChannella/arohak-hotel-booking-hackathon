"""Hotel and individual room management (problem statement section 2)."""
import uuid

import db

HOTEL_FIELDS = ("name", "address", "city", "description", "contact_number", "email", "status")
ROOM_FIELDS = ("room_number", "room_type", "capacity", "price_per_night",
               "availability_status", "description", "amenities")


class HotelStore:
    def __init__(self, path=None):
        self.path = db.database_path(path)
        db.initialize(self.path)

    # ---------- hotel ----------

    def get_hotel(self, hotel_id=db.DEFAULT_HOTEL_ID):
        with db.connect(self.path) as conn:
            row = conn.execute("SELECT * FROM hotels WHERE id=?", (hotel_id,)).fetchone()
        return dict(row) if row else None

    def update_hotel(self, payload, hotel_id=db.DEFAULT_HOTEL_ID):
        updates = {k: payload[k] for k in HOTEL_FIELDS if k in payload}
        if not updates:
            raise ValueError("No hotel fields supplied.")
        if "status" in updates and updates["status"] not in ("ACTIVE", "INACTIVE"):
            raise ValueError("Hotel status must be ACTIVE or INACTIVE.")
        for key in ("name", "address", "city"):
            if key in updates and not str(updates[key]).strip():
                raise ValueError(f"Hotel {key} cannot be empty.")
        assignments = ", ".join(f"{k}=?" for k in updates)
        with db.connect(self.path) as conn:
            cursor = conn.execute(
                f"UPDATE hotels SET {assignments} WHERE id=?",
                (*[str(v).strip() if isinstance(v, str) else v for v in updates.values()], hotel_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("Hotel not found.")
        return self.get_hotel(hotel_id)

    # ---------- rooms ----------

    def list_rooms(self, hotel_id=db.DEFAULT_HOTEL_ID, include_inactive=False):
        query = "SELECT * FROM rooms WHERE hotel_id=?"
        if not include_inactive:
            query += " AND availability_status='ACTIVE'"
        query += " ORDER BY CAST(room_number AS INTEGER)"
        with db.connect(self.path) as conn:
            rows = conn.execute(query, (hotel_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_room(self, room_id, hotel_id=None):
        """Fetch a room, optionally constrained to one hotel."""
        query = "SELECT * FROM rooms WHERE id=?"
        params = [room_id]
        if hotel_id:
            query += " AND hotel_id=?"
            params.append(hotel_id)
        with db.connect(self.path) as conn:
            row = conn.execute(query, params).fetchone()
        return dict(row) if row else None

    def create_room(self, payload, hotel_id=db.DEFAULT_HOTEL_ID):
        room_number = str(payload.get("room_number", "")).strip()
        room_type = str(payload.get("room_type", "")).strip()
        if not room_number:
            raise ValueError("Room number is required.")
        if not room_type:
            raise ValueError("Room type is required.")
        capacity = self._positive_int(payload.get("capacity"), "Capacity")
        price = self._positive_int(payload.get("price_per_night"), "Price per night", allow_zero=True)
        status = str(payload.get("availability_status", "ACTIVE")).strip().upper() or "ACTIVE"
        if status not in ("ACTIVE", "INACTIVE"):
            raise ValueError("Availability status must be ACTIVE or INACTIVE.")

        room_id = "RM-" + uuid.uuid4().hex[:8].upper()
        with db.connect(self.path) as conn:
            clash = conn.execute(
                "SELECT 1 FROM rooms WHERE hotel_id=? AND room_number=?", (hotel_id, room_number)
            ).fetchone()
            if clash:
                raise ValueError(f"Room number {room_number} already exists.")
            conn.execute(
                "INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?,?)",
                (room_id, hotel_id, room_number, room_type, capacity, price, status,
                 str(payload.get("description", "")).strip(),
                 str(payload.get("amenities", "")).strip()),
            )
        return self.get_room(room_id)

    def update_room(self, room_id, payload):
        updates = {k: payload[k] for k in ROOM_FIELDS if k in payload}
        if not updates:
            raise ValueError("No room fields supplied.")
        if "capacity" in updates:
            updates["capacity"] = self._positive_int(updates["capacity"], "Capacity")
        if "price_per_night" in updates:
            updates["price_per_night"] = self._positive_int(updates["price_per_night"], "Price per night", allow_zero=True)
        if "availability_status" in updates:
            updates["availability_status"] = str(updates["availability_status"]).strip().upper()
            if updates["availability_status"] not in ("ACTIVE", "INACTIVE"):
                raise ValueError("Availability status must be ACTIVE or INACTIVE.")
        if "room_number" in updates:
            updates["room_number"] = str(updates["room_number"]).strip()
            if not updates["room_number"]:
                raise ValueError("Room number cannot be empty.")

        existing = self.get_room(room_id)
        if not existing:
            raise LookupError("Room not found.")
        assignments = ", ".join(f"{k}=?" for k in updates)
        with db.connect(self.path) as conn:
            if "room_number" in updates:
                clash = conn.execute(
                    "SELECT 1 FROM rooms WHERE hotel_id=? AND room_number=? AND id<>?",
                    (existing["hotel_id"], updates["room_number"], room_id),
                ).fetchone()
                if clash:
                    raise ValueError(f"Room number {updates['room_number']} already exists.")
            conn.execute(f"UPDATE rooms SET {assignments} WHERE id=?", (*updates.values(), room_id))
        return self.get_room(room_id)

    def set_room_status(self, room_id, status):
        return self.update_room(room_id, {"availability_status": status})

    @staticmethod
    def _positive_int(value, label, allow_zero=False):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a whole number.")
        if number < 0 or (number == 0 and not allow_zero):
            raise ValueError(f"{label} must be a positive number.")
        return number
