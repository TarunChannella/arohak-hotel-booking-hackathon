import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import auth as auth_module
from auth import AuthError, AuthStore, PermissionError_, is_staff, require_role
from booking import BookingStore
from hotel import HotelStore
from rag import HotelRetriever


class TempDbTest(unittest.TestCase):
    """Each test class gets a throwaway database directory."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "test.db")
        self.auth = AuthStore(self.db)
        self.hotels = HotelStore(self.db)
        self.bookings = BookingStore(self.db)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def customer(self, email="guest@example.com"):
        return self.auth.register({"name": "Guest", "email": email,
                                   "password": "password123", "role": "CUSTOMER"})

    def staff(self, role="ADMIN", email=None):
        return self.auth.register({"name": role.title(), "email": email or f"{role.lower()}@example.com",
                                   "password": "password123", "role": role}, allow_staff=True)

    def dates(self, start_offset=5, nights=2):
        start = date.today() + timedelta(days=start_offset)
        return start.isoformat(), (start + timedelta(days=nights)).isoformat()


# --------------------------------------------------------------------------
# Section 1 — Authentication and roles (15 marks)
# --------------------------------------------------------------------------

class AuthTests(TempDbTest):
    def test_register_and_login(self):
        user = self.customer()
        self.assertEqual(user["role"], "CUSTOMER")
        self.assertEqual(user["organization_id"], "ORG-DEFAULT")
        token, logged_in = self.auth.login("guest@example.com", "password123")
        self.assertEqual(logged_in["id"], user["id"])
        self.assertEqual(self.auth.user_for_token(token)["email"], "guest@example.com")

    def test_password_is_never_stored_in_plain_text(self):
        self.customer()
        import db
        with db.connect(self.db) as conn:
            stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertNotIn("password123", stored)
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))

    def test_public_user_record_excludes_password_hash(self):
        self.assertNotIn("password_hash", self.customer())

    def test_wrong_password_is_rejected(self):
        self.customer()
        with self.assertRaises(AuthError):
            self.auth.login("guest@example.com", "wrong-password")

    def test_unknown_email_is_rejected(self):
        with self.assertRaises(AuthError):
            self.auth.login("nobody@example.com", "password123")

    def test_duplicate_email_is_rejected(self):
        self.customer()
        with self.assertRaises(AuthError):
            self.customer()

    def test_short_password_is_rejected(self):
        with self.assertRaises(AuthError):
            self.auth.register({"name": "A", "email": "a@example.com",
                                "password": "short", "role": "CUSTOMER"})

    def test_invalid_email_is_rejected(self):
        with self.assertRaises(AuthError):
            self.auth.register({"name": "A", "email": "not-an-email",
                                "password": "password123", "role": "CUSTOMER"})

    def test_self_registration_cannot_grant_staff_role(self):
        for role in ("ADMIN", "RECEPTIONIST"):
            with self.assertRaises(AuthError):
                self.auth.register({"name": "X", "email": f"x{role}@example.com",
                                    "password": "password123", "role": role})

    def test_logout_invalidates_session(self):
        self.customer()
        token, _ = self.auth.login("guest@example.com", "password123")
        self.auth.logout(token)
        self.assertIsNone(self.auth.user_for_token(token))

    def test_invalid_token_has_no_user(self):
        self.assertIsNone(self.auth.user_for_token("not-a-real-token"))
        self.assertIsNone(self.auth.user_for_token(None))

    def test_expired_session_is_rejected(self):
        user = self.customer()
        original = auth_module.SESSION_HOURS
        try:
            auth_module.SESSION_HOURS = -1  # already expired
            token = self.auth.create_session(user["id"])
        finally:
            auth_module.SESSION_HOURS = original
        self.assertIsNone(self.auth.user_for_token(token))

    def test_role_authorization_matrix(self):
        admin, receptionist, customer = self.staff("ADMIN"), self.staff("RECEPTIONIST"), self.customer()
        self.assertTrue(is_staff(admin))
        self.assertTrue(is_staff(receptionist))
        self.assertFalse(is_staff(customer))
        # Receptionist must not hold admin-level rights.
        with self.assertRaises(PermissionError_):
            require_role(receptionist, "ADMIN")
        with self.assertRaises(PermissionError_):
            require_role(customer, "ADMIN", "RECEPTIONIST")
        with self.assertRaises(PermissionError_):
            require_role(None, "CUSTOMER")
        require_role(admin, "ADMIN")
        require_role(receptionist, "ADMIN", "RECEPTIONIST")


# --------------------------------------------------------------------------
# Section 2 — Hotel and room management (15 marks)
# --------------------------------------------------------------------------

class HotelTests(TempDbTest):
    def test_seeded_hotel_has_every_required_field(self):
        hotel = self.hotels.get_hotel()
        for field in ("id", "name", "address", "city", "description",
                      "contact_number", "email", "status"):
            self.assertTrue(str(hotel[field]).strip(), f"{field} is empty")
        self.assertEqual(hotel["status"], "ACTIVE")

    def test_update_hotel(self):
        updated = self.hotels.update_hotel({"name": "Meridian Grand Renamed", "city": "Pune"})
        self.assertEqual(updated["name"], "Meridian Grand Renamed")
        self.assertEqual(updated["city"], "Pune")

    def test_hotel_status_is_validated(self):
        with self.assertRaises(ValueError):
            self.hotels.update_hotel({"status": "MAYBE"})

    def test_seeded_rooms_have_every_required_field(self):
        rooms = self.hotels.list_rooms()
        self.assertEqual(len(rooms), 10)
        for room in rooms:
            for field in ("id", "room_number", "room_type", "capacity",
                          "price_per_night", "availability_status", "description", "amenities"):
                self.assertTrue(str(room[field]).strip(), f"{field} is empty")

    def test_create_room(self):
        room = self.hotels.create_room({"room_number": "601", "room_type": "Penthouse",
                                        "capacity": 4, "price_per_night": 40000,
                                        "description": "Top floor", "amenities": "Wi-Fi, Terrace"})
        self.assertEqual(room["room_number"], "601")
        self.assertEqual(room["capacity"], 4)
        self.assertEqual(len(self.hotels.list_rooms()), 11)

    def test_duplicate_room_number_is_rejected(self):
        with self.assertRaises(ValueError):
            self.hotels.create_room({"room_number": "201", "room_type": "Deluxe King",
                                     "capacity": 2, "price_per_night": 8500})

    def test_room_capacity_and_price_are_validated(self):
        with self.assertRaises(ValueError):
            self.hotels.create_room({"room_number": "701", "room_type": "X",
                                     "capacity": 0, "price_per_night": 100})
        with self.assertRaises(ValueError):
            self.hotels.create_room({"room_number": "702", "room_type": "X",
                                     "capacity": "many", "price_per_night": 100})

    def test_update_room(self):
        room = self.hotels.list_rooms()[0]
        updated = self.hotels.update_room(room["id"], {"price_per_night": 9999})
        self.assertEqual(updated["price_per_night"], 9999)

    def test_update_unknown_room_raises(self):
        with self.assertRaises(LookupError):
            self.hotels.update_room("RM-DOES-NOT-EXIST", {"price_per_night": 1})

    def test_deactivated_room_is_hidden_from_customers_but_visible_to_staff(self):
        room = self.hotels.list_rooms()[0]
        self.hotels.set_room_status(room["id"], "INACTIVE")
        self.assertEqual(len(self.hotels.list_rooms()), 9)
        self.assertEqual(len(self.hotels.list_rooms(include_inactive=True)), 10)


# --------------------------------------------------------------------------
# Section 3 — Customer booking (20 marks)
# --------------------------------------------------------------------------

class BookingTests(TempDbTest):
    def test_search_returns_full_room_details(self):
        check_in, check_out = self.dates()
        rooms = self.bookings.search(check_in, check_out, 2)
        self.assertTrue(rooms)
        for field in ("room_number", "room_type", "price_per_night", "capacity",
                      "description", "amenities", "available", "total_amount"):
            self.assertIn(field, rooms[0])

    def test_search_filters_by_capacity(self):
        check_in, check_out = self.dates()
        for room in self.bookings.search(check_in, check_out, 4):
            self.assertGreaterEqual(room["capacity"], 4)

    def test_booking_has_every_required_field(self):
        customer = self.customer()
        check_in, check_out = self.dates()
        room = self.bookings.search(check_in, check_out, 2)[0]
        booking = self.bookings.create(customer, {"room_id": room["room_id"], "check_in": check_in,
                                                  "check_out": check_out, "guests": 2})
        for field in ("id", "customer_id", "organization_id", "hotel_id", "room_id",
                      "check_in", "check_out", "guests", "booking_date",
                      "total_amount", "status"):
            self.assertIn(field, booking)
        self.assertEqual(booking["status"], "CONFIRMED")
        self.assertEqual(booking["customer_id"], customer["id"])
        self.assertEqual(booking["total_amount"], room["price_per_night"] * 2)

    def test_confirmation_includes_hotel_room_and_customer(self):
        customer = self.customer()
        check_in, check_out = self.dates()
        room = self.bookings.search(check_in, check_out, 2)[0]
        booking = self.bookings.create(customer, {"room_id": room["room_id"], "check_in": check_in,
                                                  "check_out": check_out, "guests": 2})
        self.assertTrue(booking["hotel_name"])
        self.assertTrue(booking["room_number"])
        self.assertEqual(booking["customer_email"], "guest@example.com")

    def test_overlapping_booking_is_rejected(self):
        customer = self.customer()
        check_in, check_out = self.dates()
        room = self.bookings.search(check_in, check_out, 2)[0]
        payload = {"room_id": room["room_id"], "check_in": check_in, "check_out": check_out, "guests": 2}
        self.bookings.create(customer, payload)
        with self.assertRaisesRegex(ValueError, "already booked"):
            self.bookings.create(customer, payload)

    def test_partial_overlap_is_rejected(self):
        customer = self.customer()
        start = date.today() + timedelta(days=5)
        room = self.bookings.search(start.isoformat(), (start + timedelta(days=4)).isoformat(), 2)[0]
        self.bookings.create(customer, {"room_id": room["room_id"], "check_in": start.isoformat(),
                                        "check_out": (start + timedelta(days=4)).isoformat(), "guests": 2})
        with self.assertRaises(ValueError):
            self.bookings.create(customer, {
                "room_id": room["room_id"],
                "check_in": (start + timedelta(days=2)).isoformat(),
                "check_out": (start + timedelta(days=6)).isoformat(), "guests": 2})

    def test_same_day_turnover_is_allowed(self):
        customer = self.customer()
        start = date.today() + timedelta(days=5)
        middle = start + timedelta(days=2)
        end = start + timedelta(days=4)
        room = self.bookings.search(start.isoformat(), middle.isoformat(), 2)[0]
        self.bookings.create(customer, {"room_id": room["room_id"], "check_in": start.isoformat(),
                                        "check_out": middle.isoformat(), "guests": 2})
        second = self.bookings.create(customer, {"room_id": room["room_id"], "check_in": middle.isoformat(),
                                                 "check_out": end.isoformat(), "guests": 2})
        self.assertEqual(second["status"], "CONFIRMED")

    def test_booked_room_disappears_from_search(self):
        customer = self.customer()
        check_in, check_out = self.dates()
        room = self.bookings.search(check_in, check_out, 2)[0]
        self.bookings.create(customer, {"room_id": room["room_id"], "check_in": check_in,
                                        "check_out": check_out, "guests": 2})
        remaining = [r["room_id"] for r in self.bookings.search(check_in, check_out, 2)]
        self.assertNotIn(room["room_id"], remaining)

    def test_capacity_is_enforced_at_booking(self):
        customer = self.customer()
        check_in, check_out = self.dates()
        room = next(r for r in self.bookings.search(check_in, check_out, 1) if r["capacity"] == 2)
        with self.assertRaisesRegex(ValueError, "maximum capacity"):
            self.bookings.create(customer, {"room_id": room["room_id"], "check_in": check_in,
                                            "check_out": check_out, "guests": 4})

    def test_inactive_room_cannot_be_booked(self):
        customer = self.customer()
        check_in, check_out = self.dates()
        room = self.bookings.search(check_in, check_out, 2)[0]
        self.hotels.set_room_status(room["room_id"], "INACTIVE")
        with self.assertRaisesRegex(ValueError, "not available"):
            self.bookings.create(customer, {"room_id": room["room_id"], "check_in": check_in,
                                            "check_out": check_out, "guests": 2})

    def test_invalid_date_ranges_are_rejected(self):
        customer = self.customer()
        room_id = self.bookings.search(*self.dates(), 2)[0]["room_id"]
        past = (date.today() - timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):  # check-out before check-in
            self.bookings.create(customer, {"room_id": room_id, "check_in": self.dates()[1],
                                            "check_out": self.dates()[0], "guests": 2})
        with self.assertRaises(ValueError):  # check-in in the past
            self.bookings.create(customer, {"room_id": room_id, "check_in": past,
                                            "check_out": self.dates()[1], "guests": 2})
        with self.assertRaises(ValueError):  # malformed
            self.bookings.create(customer, {"room_id": room_id, "check_in": "not-a-date",
                                            "check_out": self.dates()[1], "guests": 2})

    def test_customers_only_see_their_own_bookings(self):
        first, second = self.customer("one@example.com"), self.customer("two@example.com")
        check_in, check_out = self.dates()
        rooms = self.bookings.search(check_in, check_out, 2)
        self.bookings.create(first, {"room_id": rooms[0]["room_id"], "check_in": check_in,
                                     "check_out": check_out, "guests": 2})
        self.bookings.create(second, {"room_id": rooms[1]["room_id"], "check_in": check_in,
                                      "check_out": check_out, "guests": 2})
        self.assertEqual(len(self.bookings.list_for_customer(first["id"])), 1)
        self.assertEqual(len(self.bookings.list_for_customer(second["id"])), 1)
        self.assertEqual(len(self.bookings.list_all()), 2)

    def test_simultaneous_booking_attempts_yield_exactly_one_winner(self):
        """Ten threads race for the same room; only one may win."""
        customer = self.customer()
        check_in, check_out = self.dates()
        room = self.bookings.search(check_in, check_out, 2)[0]
        payload = {"room_id": room["room_id"], "check_in": check_in,
                   "check_out": check_out, "guests": 2}
        results, lock = [], threading.Lock()
        barrier = threading.Barrier(10)

        def attempt():
            store = BookingStore(self.db)
            barrier.wait()
            try:
                store.create(customer, payload)
                outcome = "created"
            except Exception:
                outcome = "rejected"
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("created"), 1, f"expected exactly one winner, got {results}")
        self.assertEqual(len(self.bookings.list_all()), 1)


# --------------------------------------------------------------------------
# Cancellation and staff review
# --------------------------------------------------------------------------

class CancellationTests(TempDbTest):
    def book(self, offset, customer=None):
        customer = customer or self.customer()
        start = date.today() + timedelta(days=offset)
        check_in, check_out = start.isoformat(), (start + timedelta(days=2)).isoformat()
        room = self.bookings.search(check_in, check_out, 2)[0]
        booking = self.bookings.create(customer, {"room_id": room["room_id"], "check_in": check_in,
                                                  "check_out": check_out, "guests": 2})
        return customer, booking, check_in, check_out

    def test_deadline_matches_the_documented_example(self):
        # "For a booking with check-in on 20 September, direct cancellation is
        # available until 19 September."
        self.assertEqual(self.bookings.direct_cancellation_deadline("2026-09-20"),
                         date(2026, 9, 19))

    def test_direct_cancellation_before_deadline(self):
        customer, booking, _, _ = self.book(5)
        self.assertEqual(self.bookings.cancel(booking["id"], customer["id"])["status"], "CANCELLED")

    def test_cancellation_one_day_before_check_in_is_still_direct(self):
        customer, booking, _, _ = self.book(1)
        self.assertEqual(self.bookings.cancel(booking["id"], customer["id"])["status"], "CANCELLED")

    def test_late_cancellation_becomes_a_staff_request(self):
        customer, booking, _, _ = self.book(0)
        self.assertEqual(self.bookings.cancel(booking["id"], customer["id"])["status"],
                         "CANCELLATION_REQUESTED")

    def test_cancelled_booking_releases_the_room(self):
        customer, booking, check_in, check_out = self.book(5)
        self.bookings.cancel(booking["id"], customer["id"])
        self.assertIn(booking["room_id"], [r["room_id"] for r in self.bookings.search(check_in, check_out, 2)])

    def test_pending_request_keeps_holding_the_room(self):
        customer, booking, check_in, check_out = self.book(0)
        self.bookings.cancel(booking["id"], customer["id"])
        self.assertNotIn(booking["room_id"],
                         [r["room_id"] for r in self.bookings.search(check_in, check_out, 2)])

    def test_cancelling_twice_is_rejected(self):
        customer, booking, _, _ = self.book(5)
        self.bookings.cancel(booking["id"], customer["id"])
        with self.assertRaisesRegex(ValueError, "already cancelled"):
            self.bookings.cancel(booking["id"], customer["id"])

    def test_duplicate_request_is_rejected(self):
        customer, booking, _, _ = self.book(0)
        self.bookings.cancel(booking["id"], customer["id"])
        with self.assertRaisesRegex(ValueError, "awaiting staff review"):
            self.bookings.cancel(booking["id"], customer["id"])

    def test_customer_cannot_cancel_another_customers_booking(self):
        customer, booking, _, _ = self.book(5)
        intruder = self.customer("intruder@example.com")
        with self.assertRaises(LookupError):
            self.bookings.cancel(booking["id"], intruder["id"])

    def test_staff_approval_cancels_and_releases_the_room(self):
        customer, booking, check_in, check_out = self.book(0)
        self.bookings.cancel(booking["id"], customer["id"])
        self.assertEqual(len(self.bookings.list_cancellation_requests()), 1)
        reviewed = self.bookings.review_cancellation(booking["id"], approve=True)
        self.assertEqual(reviewed["status"], "CANCELLED")
        self.assertIn(booking["room_id"], [r["room_id"] for r in self.bookings.search(check_in, check_out, 2)])

    def test_staff_rejection_restores_the_confirmed_booking(self):
        customer, booking, check_in, check_out = self.book(0)
        self.bookings.cancel(booking["id"], customer["id"])
        reviewed = self.bookings.review_cancellation(booking["id"], approve=False)
        self.assertEqual(reviewed["status"], "CONFIRMED")
        self.assertNotIn(booking["room_id"],
                         [r["room_id"] for r in self.bookings.search(check_in, check_out, 2)])
        self.assertEqual(self.bookings.list_cancellation_requests(), [])

    def test_review_requires_a_pending_request(self):
        customer, booking, _, _ = self.book(5)
        with self.assertRaisesRegex(ValueError, "no cancellation request"):
            self.bookings.review_cancellation(booking["id"], approve=True)

    def test_unknown_booking_raises(self):
        with self.assertRaises(LookupError):
            self.bookings.cancel("MGM-NOPE")

    def test_past_stay_is_marked_completed(self):
        customer = self.customer()
        check_in, check_out = self.dates()
        room = self.bookings.search(check_in, check_out, 2)[0]
        booking = self.bookings.create(customer, {"room_id": room["room_id"], "check_in": check_in,
                                                  "check_out": check_out, "guests": 2})
        import db
        past_in = (date.today() - timedelta(days=5)).isoformat()
        past_out = (date.today() - timedelta(days=3)).isoformat()
        with db.connect(self.db) as conn:
            conn.execute("UPDATE bookings SET check_in=?, check_out=? WHERE id=?",
                         (past_in, past_out, booking["id"]))
        self.bookings.mark_completed()
        self.assertEqual(self.bookings.get(booking["id"])["status"], "COMPLETED")


# --------------------------------------------------------------------------
# Grounded PDF chatbot (preserved from the starter)
# --------------------------------------------------------------------------

class RagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rag = HotelRetriever()

    def test_grounded_checkin(self):
        result = self.rag.answer("What time is check in?")
        self.assertTrue(result["grounded"])
        self.assertIn("2:00 PM", result["answer"])
        self.assertTrue(result["citations"])

    def test_unknown_is_refused(self):
        result = self.rag.answer("Does the hotel have a casino?")
        self.assertFalse(result["grounded"])
        self.assertIn("not available", result["answer"])
        self.assertEqual(result["citations"], [])

    def test_four_guests_retrieves_rooms(self):
        self.assertIn("Family Suite", self.rag.answer("Which room is suitable for four guests?")["answer"])

    def test_late_checkout_charge(self):
        result = self.rag.answer("What happens if I check out at 4 PM?")
        self.assertIn("50%", result["answer"])
        self.assertTrue(result["grounded"])

    def test_parking_is_grounded(self):
        result = self.rag.answer("Is parking free?")
        self.assertTrue(result["grounded"])
        self.assertIn("complimentary", result["answer"].lower())

    def test_every_grounded_answer_carries_a_citation(self):
        for question in ("Is Wi-Fi free?", "Is there a swimming pool?", "What time is check-out?"):
            result = self.rag.answer(question)
            if result["grounded"]:
                self.assertTrue(result["citations"], f"no citation for: {question}")
                self.assertIn("section", result["citations"][0])


if __name__ == "__main__":
    unittest.main()
