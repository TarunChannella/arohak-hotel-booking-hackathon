"""Database schema, connection handling and seed data.

organization_id is present on users, hotels and bookings from the start so the
multi-organization extension (problem statement section 5) can be layered on
without rebuilding the MVP.
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_ORG_ID = "ORG-DEFAULT"
DEFAULT_HOTEL_ID = "HGMUM001"

SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('ADMIN','RECEPTIONIST','CUSTOMER')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hotels (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    contact_number TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','INACTIVE'))
);

CREATE TABLE IF NOT EXISTS rooms (
    id TEXT PRIMARY KEY,
    hotel_id TEXT NOT NULL REFERENCES hotels(id),
    room_number TEXT NOT NULL,
    room_type TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    price_per_night INTEGER NOT NULL CHECK (price_per_night >= 0),
    availability_status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (availability_status IN ('ACTIVE','INACTIVE')),
    description TEXT NOT NULL DEFAULT '',
    amenities TEXT NOT NULL DEFAULT '',
    UNIQUE (hotel_id, room_number)
);

CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES users(id),
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    hotel_id TEXT NOT NULL REFERENCES hotels(id),
    room_id TEXT NOT NULL REFERENCES rooms(id),
    check_in TEXT NOT NULL,
    check_out TEXT NOT NULL,
    guests INTEGER NOT NULL CHECK (guests > 0),
    booking_date TEXT NOT NULL,
    total_amount INTEGER NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('CONFIRMED','CANCELLED','COMPLETED','CANCELLATION_REQUESTED'))
);

CREATE INDEX IF NOT EXISTS idx_bookings_room_dates ON bookings(room_id, check_in, check_out);
CREATE INDEX IF NOT EXISTS idx_bookings_customer ON bookings(customer_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""

# Room inventory for the seeded hotel. Types, capacities and prices come from
# section 4 of the hotel PDF; room numbers and per-type counts are demo data.
SEED_ROOMS = [
    ("201", "Deluxe King", 2, 8500, "City-view room on the second floor with a king bed and work desk."),
    ("202", "Deluxe King", 2, 8500, "City-view room on the second floor with a king bed and work desk."),
    ("203", "Deluxe Twin", 2, 8500, "City-view room with two twin beds and a work desk."),
    ("204", "Deluxe Twin", 2, 8500, "City-view room with two twin beds and a work desk."),
    ("301", "Premier Sea View", 3, 12500, "Sea-facing room with a king bed and sofa chair."),
    ("302", "Premier Sea View", 3, 12500, "Sea-facing room with a king bed and sofa chair."),
    ("401", "Executive Suite", 3, 18000, "Suite with a separate living area and sea view."),
    ("402", "Executive Suite", 3, 18000, "Suite with a separate living area and sea view."),
    ("501", "Family Suite", 4, 22000, "Two-bedroom suite with living area and dining table."),
    ("502", "Family Suite", 4, 22000, "Two-bedroom suite with living area and dining table."),
]

BASE_AMENITIES = "Wi-Fi, Air conditioning, Smart TV, Tea/coffee setup, In-room safe, Hair dryer, Mini refrigerator, Daily housekeeping"
SUITE_AMENITIES = BASE_AMENITIES + ", Separate living area, Bathrobe and slippers, Premium bathroom amenities, Evening turndown service"


def database_path(path=None):
    return str(path or os.environ.get("HOTEL_DB") or Path(__file__).parent / "data" / "hotel.db")


@contextmanager
def connect(path):
    """Yield a connection. Commits on success, rolls back on error."""
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(path):
    """Create tables and seed the default organization, hotel and rooms."""
    from datetime import UTC, datetime

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO organizations VALUES (?,?,?,?)",
            (DEFAULT_ORG_ID, "Meridian Hospitality Group", "ACTIVE", now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO hotels VALUES (?,?,?,?,?,?,?,?,?)",
            (DEFAULT_HOTEL_ID, DEFAULT_ORG_ID, "The Meridian Grand Mumbai",
             "18 Marine View Road, Nariman Point", "Mumbai",
             "Sea-facing five-star hotel at Nariman Point with 24-hour reception.",
             "+91 22 4567 8900", "reservations@meridiangrand.example", "ACTIVE"),
        )
        for number, room_type, capacity, price, description in SEED_ROOMS:
            amenities = SUITE_AMENITIES if "Suite" in room_type else BASE_AMENITIES
            conn.execute(
                "INSERT OR IGNORE INTO rooms VALUES (?,?,?,?,?,?,?,?,?)",
                (f"RM-{number}", DEFAULT_HOTEL_ID, number, room_type, capacity,
                 price, "ACTIVE", description, amenities),
            )
