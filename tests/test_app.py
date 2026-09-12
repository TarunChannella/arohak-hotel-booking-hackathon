import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import pathlib

import auth as auth_module
import db
import ingest
import rag as rag_module
from auth import AuthError, AuthStore, PermissionError_, is_staff, require_role
from assistant import (BookingAssistant, ToolLayer, extract_booking_id,
                       extract_dates, extract_guests, extract_location)
from booking import BookingStore
from hotel import HotelStore
from organization import OrganizationStore
from rag import HotelRetriever


class TempDbTest(unittest.TestCase):
    """Each test class gets a throwaway database directory."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "test.db")
        self._previous_db = os.environ.get("HOTEL_DB")
        os.environ["HOTEL_DB"] = self.db
        self.auth = AuthStore(self.db)
        self.hotels = HotelStore(self.db)
        self.bookings = BookingStore(self.db)

    def tearDown(self):
        if self._previous_db is None:
            os.environ.pop("HOTEL_DB", None)
        else:
            os.environ["HOTEL_DB"] = self._previous_db
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

class RagTests(TempDbTest):
    def setUp(self):
        super().setUp()
        self.rag = HotelRetriever()

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


# --------------------------------------------------------------------------
# Grounded chatbot correctness (defects reported in browser QA)
# --------------------------------------------------------------------------

class RagCorrectnessTests(TempDbTest):
    def setUp(self):
        super().setUp()
        self.rag = HotelRetriever()

    def test_checkin_answer_leads_with_checkin_not_checkout(self):
        for question in ("What time is check-in?", "What time is check in?", "When is checkin?"):
            result = self.rag.answer(question)
            with self.subTest(question=question):
                self.assertTrue(result["grounded"])
                self.assertIn("2:00 PM", result["answer"])
                self.assertNotIn("12:00 PM", result["answer"])
                self.assertFalse(result["answer"].lower().startswith("standard check-out"))
                self.assertIn("Check-in", result["citations"][0]["section"])

    def test_checkout_answer_is_about_checkout(self):
        result = self.rag.answer("What time is check-out?")
        self.assertTrue(result["grounded"])
        self.assertIn("12:00 PM", result["answer"])
        self.assertIn("Check-out", result["citations"][0]["section"])

    def test_named_room_over_capacity_is_refused(self):
        result = self.rag.answer("Can four guests stay in a Deluxe King room?")
        self.assertTrue(result["grounded"])
        self.assertTrue(result["answer"].startswith("No."), result["answer"])
        self.assertIn("Deluxe King", result["answer"])
        self.assertIn("maximum capacity of 2", result["answer"])
        self.assertEqual(result["citations"][0]["section"], "4. Room Categories")

    def test_named_room_within_capacity_is_confirmed(self):
        result = self.rag.answer("Can two guests stay in a Deluxe King room?")
        self.assertTrue(result["answer"].startswith("Yes."), result["answer"])
        self.assertIn("Deluxe King", result["answer"])

    def test_room_for_four_guests_suggests_family_suite(self):
        self.assertIn("Family Suite", self.rag.answer("Which room is suitable for four guests?")["answer"])

    def test_capacity_beyond_every_room_is_refused(self):
        result = self.rag.answer("Which room is suitable for five guests?")
        self.assertIn("No room category", result["answer"])
        self.assertIn("Family Suite", result["answer"])

    def test_parking_answer(self):
        result = self.rag.answer("Is parking free?")
        self.assertTrue(result["grounded"])
        self.assertIn("complimentary", result["answer"].lower())
        self.assertTrue(result["citations"])

    def test_wifi_answer(self):
        result = self.rag.answer("Is Wi-Fi free?")
        self.assertTrue(result["grounded"])
        self.assertIn("wi-fi", result["answer"].lower())

    def test_cancellation_policy_states_the_24_hour_rule(self):
        result = self.rag.answer("What is the cancellation policy?")
        self.assertTrue(result["grounded"])
        self.assertIn("24 hours", result["answer"])

    def test_faq_section_is_present_in_the_knowledge_base(self):
        sections = [s["section"] for s in self.rag.sections]
        self.assertTrue(any(s.startswith("11.") for s in sections), sections)

    def test_answers_never_echo_raw_faq_question_markers(self):
        for question in ("Is parking free?", "Is Wi-Fi free?", "Is breakfast included?",
                         "Is the gym open 24 hours?", "Can I add an extra bed?"):
            answer = self.rag.answer(question)["answer"]
            with self.subTest(question=question):
                self.assertNotIn("Q:", answer)
                self.assertNotIn("A:", answer)

    def test_unavailable_information_is_refused_without_citations(self):
        for question in ("Does the hotel have a casino?", "Is there a golf course?",
                         "Do you allow pet elephants?"):
            result = self.rag.answer(question)
            with self.subTest(question=question):
                self.assertFalse(result["grounded"])
                self.assertIn("not available", result["answer"])
                self.assertEqual(result["citations"], [])

    def test_every_grounded_answer_has_a_citation_with_an_excerpt(self):
        for question in ("What time is check-in?", "Is parking free?", "Is Wi-Fi free?",
                         "What is the cancellation policy?", "Is there a spa?",
                         "Can four guests stay in a Deluxe King room?"):
            result = self.rag.answer(question)
            with self.subTest(question=question):
                self.assertTrue(result["grounded"])
                self.assertTrue(result["citations"])
                self.assertTrue(result["citations"][0]["section"])
                self.assertTrue(result["citations"][0]["excerpt"])


# --------------------------------------------------------------------------
# Section 4 — Booking dashboards
# --------------------------------------------------------------------------

class DashboardTests(TempDbTest):
    def make_booking(self, customer, offset=5):
        start = date.today() + timedelta(days=offset)
        check_in, check_out = start.isoformat(), (start + timedelta(days=2)).isoformat()
        room = self.bookings.search(check_in, check_out, 2)[0]
        return self.bookings.create(customer, {"room_id": room["room_id"], "check_in": check_in,
                                               "check_out": check_out, "guests": 2})

    def test_customer_sees_upcoming_completed_and_cancelled(self):
        import db
        customer = self.customer()
        upcoming = self.make_booking(customer, 5)
        cancelled = self.make_booking(customer, 7)
        self.bookings.cancel(cancelled["id"], customer["id"])
        past = self.make_booking(customer, 9)
        with db.connect(self.db) as conn:
            conn.execute("UPDATE bookings SET check_in=?, check_out=? WHERE id=?",
                         ((date.today() - timedelta(days=5)).isoformat(),
                          (date.today() - timedelta(days=3)).isoformat(), past["id"]))
        self.bookings.mark_completed()
        rows = {b["id"]: b["display_status"] for b in self.bookings.list_for_customer(customer["id"])}
        self.assertEqual(rows[upcoming["id"]], "CONFIRMED")
        self.assertEqual(rows[cancelled["id"]], "CANCELLED")
        self.assertEqual(rows[past["id"]], "COMPLETED")

    def test_booking_details_carry_everything_a_dashboard_shows(self):
        booking = self.make_booking(self.customer())
        for field in ("id", "hotel_name", "room_number", "room_type", "check_in", "check_out",
                      "guests", "total_amount", "status", "display_status", "can_cancel_directly"):
            self.assertIn(field, booking)

    def test_staff_can_filter_bookings_by_status(self):
        customer = self.customer()
        confirmed = self.make_booking(customer, 5)
        cancelled = self.make_booking(customer, 7)
        self.bookings.cancel(cancelled["id"], customer["id"])
        ids = [b["id"] for b in self.bookings.list_all(status="CONFIRMED")]
        self.assertIn(confirmed["id"], ids)
        self.assertNotIn(cancelled["id"], ids)
        self.assertEqual([b["id"] for b in self.bookings.list_all(status="CANCELLED")], [cancelled["id"]])

    def test_staff_can_search_bookings_by_name_email_and_reference(self):
        customer = self.customer("searchable@example.com")
        booking = self.make_booking(customer)
        for term in ("Guest", "searchable@example.com", booking["id"]):
            with self.subTest(term=term):
                self.assertEqual([b["id"] for b in self.bookings.list_all(query=term)], [booking["id"]])
        self.assertEqual(self.bookings.list_all(query="nobody-matches-this"), [])


# --------------------------------------------------------------------------
# Section 6 — Controlled AI booking assistant
# --------------------------------------------------------------------------

class AssistantExtractionTests(unittest.TestCase):
    def test_extracts_guests_dates_and_location_from_the_example_sentence(self):
        sentence = "I need a room in Mumbai for 2 people from Sept 20 to Sept 23."
        check_in, check_out = extract_dates(sentence, today=date(2026, 9, 12))
        self.assertEqual(extract_guests(sentence), 2)
        self.assertEqual(extract_location(sentence, "Mumbai"), "Mumbai")
        self.assertEqual(check_in, date(2026, 9, 20))
        self.assertEqual(check_out, date(2026, 9, 23))

    def test_extracts_iso_dates(self):
        self.assertEqual(extract_dates("book 2026-09-20 to 2026-09-23", today=date(2026, 9, 12)),
                         (date(2026, 9, 20), date(2026, 9, 23)))

    def test_extracts_relative_dates_and_night_counts(self):
        self.assertEqual(extract_dates("tomorrow for 3 nights", today=date(2026, 9, 12)),
                         (date(2026, 9, 13), date(2026, 9, 16)))

    def test_month_name_is_not_mistaken_for_a_city(self):
        self.assertIsNone(extract_location("from Sept 25 to Sept 27", "Mumbai"))

    def test_guest_words_and_digits_both_work(self):
        self.assertEqual(extract_guests("for two guests"), 2)
        self.assertEqual(extract_guests("4 people"), 4)
        self.assertIsNone(extract_guests("a quiet room"))

    def test_extracts_booking_reference(self):
        self.assertEqual(extract_booking_id("cancel MGM-ABCD1234 please"), "MGM-ABCD1234")
        self.assertIsNone(extract_booking_id("cancel my booking"))


class AssistantTests(TempDbTest):
    def setUp(self):
        super().setUp()
        self.assistant = BookingAssistant(ToolLayer(self.bookings, self.hotels))
        self.user = self.customer()

    def say(self, message, user=None):
        return self.assistant.respond(user or self.user, message)

    def test_search_requires_confirmation_before_booking(self):
        reply = self.say("I need a room in Mumbai for 2 people from Sept 20 to Sept 23.")
        self.assertTrue(reply["requires_confirmation"])
        self.assertEqual(reply["action"], "create")
        # Nothing is booked until the customer confirms.
        self.assertEqual(self.bookings.list_for_customer(self.user["id"]), [])

    def test_confirmation_creates_the_booking(self):
        self.say("I need a room in Mumbai for 2 people from Sept 20 to Sept 23.")
        reply = self.say("yes")
        self.assertEqual(reply["action"], "created")
        self.assertEqual(len(self.bookings.list_for_customer(self.user["id"])), 1)

    def test_declining_makes_no_change(self):
        self.say("I need a room in Mumbai for 2 people from Sept 20 to Sept 23.")
        reply = self.say("no")
        self.assertIsNone(reply["action"])
        self.assertEqual(self.bookings.list_for_customer(self.user["id"]), [])

    def test_confirmation_without_a_proposal_does_nothing(self):
        reply = self.say("yes")
        self.assertIn("nothing waiting", reply["reply"])
        self.assertEqual(self.bookings.list_for_customer(self.user["id"]), [])

    def test_assistant_asks_for_missing_details(self):
        reply = self.say("I need a room for 3 people")
        self.assertIn("check-in date", reply["reply"])
        self.assertFalse(reply["requires_confirmation"])

    def test_follow_up_completes_the_search(self):
        self.say("I need a room for 3 people")
        reply = self.say("from Sept 25 to Sept 27")
        self.assertTrue(reply["requires_confirmation"])
        self.assertIn("available", reply["reply"])

    def test_other_cities_are_refused_rather_than_invented(self):
        reply = self.say("I need a room in Delhi for 2 people from Sept 20 to Sept 23.")
        self.assertIn("Delhi", reply["reply"])
        self.assertFalse(reply["requires_confirmation"])

    def test_availability_is_never_invented_when_nothing_is_free(self):
        # Take every room for the window, then ask.
        check_in = (date.today() + timedelta(days=30)).isoformat()
        check_out = (date.today() + timedelta(days=32)).isoformat()
        for room in self.bookings.search(check_in, check_out, 1):
            self.bookings.create(self.user, {"room_id": room["room_id"], "check_in": check_in,
                                             "check_out": check_out, "guests": 1})
        reply = self.say(f"I need a room for 2 people from {check_in} to {check_out}")
        self.assertIn("no rooms", reply["reply"].lower())
        self.assertFalse(reply["requires_confirmation"])

    def test_lists_upcoming_bookings(self):
        self.say("I need a room in Mumbai for 2 people from Sept 20 to Sept 23.")
        self.say("yes")
        reply = self.say("show my upcoming bookings")
        self.assertIn("1 upcoming booking", reply["reply"])

    def test_booking_details_by_reference(self):
        self.say("I need a room in Mumbai for 2 people from Sept 20 to Sept 23.")
        booking_id = self.say("yes")["data"]["booking"]["id"]
        reply = self.say(f"show details for {booking_id}")
        self.assertIn(booking_id, reply["reply"])

    def test_cancellation_requires_confirmation_then_cancels(self):
        self.say("I need a room in Mumbai for 2 people from Sept 20 to Sept 23.")
        booking_id = self.say("yes")["data"]["booking"]["id"]
        proposal = self.say("cancel my booking")
        self.assertTrue(proposal["requires_confirmation"])
        self.assertEqual(proposal["action"], "cancel")
        self.assertEqual(self.bookings.get(booking_id)["status"], "CONFIRMED")
        reply = self.say("yes")
        self.assertEqual(reply["action"], "cancelled")
        self.assertEqual(self.bookings.get(booking_id)["status"], "CANCELLED")

    def test_assistant_cannot_reach_another_customers_booking(self):
        self.say("I need a room in Mumbai for 2 people from Sept 20 to Sept 23.")
        booking_id = self.say("yes")["data"]["booking"]["id"]
        intruder = self.customer("intruder@example.com")
        reply = self.say(f"show details for {booking_id}", user=intruder)
        self.assertIn("could not find", reply["reply"])
        reply = self.say(f"cancel {booking_id}", user=intruder)
        self.assertIn("could not find", reply["reply"])
        self.assertEqual(self.bookings.get(booking_id)["status"], "CONFIRMED")

    def test_pending_action_is_per_user(self):
        self.say("I need a room in Mumbai for 2 people from Sept 20 to Sept 23.")
        other = self.customer("other@example.com")
        # The other customer's "yes" must not execute this customer's proposal.
        self.say("yes", user=other)
        self.assertEqual(self.bookings.list_for_customer(other["id"]), [])
        self.assertEqual(self.bookings.list_for_customer(self.user["id"]), [])

    def test_tool_layer_exposes_no_database_handle(self):
        tools = ToolLayer(self.bookings, self.hotels)
        public = sorted(name for name in dir(tools) if not name.startswith("_"))
        self.assertEqual(public, sorted([
            "cancel_booking", "check_availability", "create_booking", "get_booking",
            "get_hotel", "list_bookings", "search_rooms"]))

    def test_unknown_requests_are_handled_gracefully(self):
        reply = self.say("what is the weather in Paris")
        self.assertFalse(reply["requires_confirmation"])
        self.assertIn("I can search for rooms", reply["reply"])


def build_minimal_pdf(lines):
    """Build a small but valid text PDF so upload tests use real extraction."""
    content = ["BT", "/F1 12 Tf", "50 740 Td", "14 TL"]
    for line in lines:
        escaped = str(line).replace("\\", "").replace("(", "").replace(")", "")
        content.append(f"({escaped}) Tj T*")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode() +
            b" /Root 1 0 R >>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF\n")
    return bytes(out)



# --------------------------------------------------------------------------
# Section 7 — PDF ingestion: the PDF is the source of truth
# --------------------------------------------------------------------------

class PdfIngestionTests(TempDbTest):
    """Every test here runs the actual supplied hotel PDF through ingest.py."""

    def test_supplied_pdf_exists_and_text_is_extracted(self):
        self.assertTrue(ingest.SUPPLIED_PDF.exists(), ingest.SUPPLIED_PDF)
        pages = ingest.extract_pages(ingest.SUPPLIED_PDF)
        self.assertEqual(len(pages), 4)
        combined = " ".join(text for _, text in pages)
        # Phrases that exist only in the PDF, not in any Python source file.
        for phrase in ("Standard check-in time is 2:00 PM",
                       "MeridianGuest",
                       "Chhatrapati Shivaji Maharaj International Airport",
                       "Skyline 18"):
            self.assertIn(phrase, combined, f"missing from extracted PDF text: {phrase}")

    def test_index_is_built_from_extracted_pdf_chunks(self):
        chunks, pages = ingest.build_index(ingest.SUPPLIED_PDF)
        self.assertEqual(pages, 4)
        self.assertGreaterEqual(len(chunks), 10)
        for chunk in chunks:
            self.assertIn("section", chunk)
            self.assertIn("page", chunk)
            self.assertIn("text", chunk)
            self.assertTrue(1 <= chunk["page"] <= 4)
            self.assertTrue(chunk["text"].strip())
        headings = [c["section"] for c in chunks]
        for expected in ("1. Hotel Overview", "4. Room Categories",
                         "9. Wi-Fi and Connectivity", "11. Frequently Asked Questions"):
            self.assertIn(expected, headings)

    def test_headings_keep_the_page_they_came_from(self):
        chunks, _ = ingest.build_index(ingest.SUPPLIED_PDF)
        pages = {c["section"]: c["page"] for c in chunks}
        self.assertEqual(pages["1. Hotel Overview"], 1)
        self.assertEqual(pages["4. Room Categories"], 2)
        self.assertEqual(pages["9. Wi-Fi and Connectivity"], 3)

    def test_room_capacities_are_parsed_from_the_pdf_table(self):
        chunks, _ = ingest.build_index(ingest.SUPPLIED_PDF)
        capacities = ingest.room_capacities(chunks)
        self.assertEqual(capacities["Deluxe King"], 2)
        self.assertEqual(capacities["Premier Sea View"], 3)
        self.assertEqual(capacities["Family Suite"], 4)

    def test_no_hand_written_knowledge_file_is_used(self):
        legacy = pathlib.Path(ingest.__file__).parent / "data" / "hotel_knowledge.json"
        self.assertFalse(legacy.exists(),
                         "hotel_knowledge.json must not be the authoritative source")
        source = pathlib.Path(rag_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("hotel_knowledge", source)

    def test_cache_is_derived_from_the_pdf_and_rebuilds_when_deleted(self):
        ingest.load_index()
        cache = ingest.cache_path(db.DEFAULT_HOTEL_ID)
        self.assertTrue(cache.exists())
        record = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(record["source_sha256"],
                         ingest.file_digest(ingest.stored_pdf_path(db.DEFAULT_HOTEL_ID)))
        cache.unlink()
        self.assertTrue(ingest.load_index())      # rebuilt from the PDF
        self.assertTrue(cache.exists())

    def test_document_is_associated_with_the_hotel_id(self):
        ingest.load_index()
        info = ingest.document_info(db.DEFAULT_HOTEL_ID)
        self.assertEqual(info["hotel_id"], db.DEFAULT_HOTEL_ID)
        self.assertEqual(info["pages"], 4)
        self.assertGreaterEqual(info["chunk_count"], 10)


class PdfGroundedAnswerTests(TempDbTest):
    def setUp(self):
        super().setUp()
        self.rag = HotelRetriever()

    def chunk_text(self):
        return " ".join(c["text"] for c in self.rag.sections)

    def test_index_came_from_the_pdf(self):
        self.assertGreaterEqual(len(self.rag.sections), 10)
        self.assertIn("MeridianGuest", self.chunk_text())

    def test_checkin_answer_is_pdf_evidence_with_page_citation(self):
        result = self.rag.answer("What time is check-in?")
        self.assertTrue(result["grounded"])
        self.assertIn("2:00 PM", result["answer"])
        self.assertNotIn("12:00 PM", result["answer"])
        citation = result["citations"][0]
        self.assertIn("Check-in", citation["section"])
        self.assertEqual(citation["page"], 1)
        # The answer sentence is present verbatim in the extracted PDF text.
        self.assertIn(result["answer"].rstrip("."), self.chunk_text())

    def test_wifi_answer_is_pdf_evidence_with_page_citation(self):
        result = self.rag.answer("Is Wi-Fi free?")
        self.assertTrue(result["grounded"])
        self.assertIn("wi-fi", result["answer"].lower())
        self.assertTrue(all(c["page"] for c in result["citations"]))

    def test_parking_answer_is_pdf_evidence_with_page_citation(self):
        result = self.rag.answer("Is parking free?")
        self.assertTrue(result["grounded"])
        self.assertIn("complimentary", result["answer"].lower())
        self.assertEqual(result["citations"][0]["page"], 3)

    def test_cancellation_answer_is_pdf_evidence(self):
        result = self.rag.answer("What is the cancellation policy?")
        self.assertTrue(result["grounded"])
        self.assertIn("Cancellation", result["citations"][0]["section"])
        self.assertEqual(result["citations"][0]["page"], 2)

    def test_named_room_capacity_comes_from_the_pdf_table(self):
        result = self.rag.answer("Can four guests stay in a Deluxe King room?")
        self.assertTrue(result["answer"].startswith("No."))
        self.assertIn("maximum capacity of 2", result["answer"])
        self.assertIn("Room Categories", result["citations"][0]["section"])

    def test_every_grounded_answer_reports_section_and_page(self):
        for question in ("What time is check-in?", "Is Wi-Fi free?", "Is parking free?",
                         "What is the cancellation policy?", "Is there a swimming pool?"):
            result = self.rag.answer(question)
            with self.subTest(question=question):
                self.assertTrue(result["grounded"])
                for citation in result["citations"]:
                    self.assertTrue(citation["section"])
                    self.assertTrue(citation["page"])
                    self.assertTrue(citation["excerpt"])

    def test_information_absent_from_the_pdf_is_refused(self):
        for question in ("Does the hotel have a casino?", "Is there a golf course?",
                         "What is the helipad fee?"):
            result = self.rag.answer(question)
            with self.subTest(question=question):
                self.assertFalse(result["grounded"])
                self.assertIn("not available", result["answer"])
                self.assertEqual(result["citations"], [])

    def test_pdf_content_does_not_drive_availability(self):
        """The PDF lists five room categories; availability comes from SQLite."""
        check_in = (date.today() + timedelta(days=3)).isoformat()
        check_out = (date.today() + timedelta(days=5)).isoformat()
        before = len(self.bookings.search(check_in, check_out, 2))
        for room in self.bookings.search(check_in, check_out, 1):
            self.hotels.set_room_status(room["room_id"], "INACTIVE")
        after = len(self.bookings.search(check_in, check_out, 2))
        self.assertGreater(before, 0)
        self.assertEqual(after, 0)
        # The chatbot still answers from the PDF, unaffected by live inventory.
        self.assertTrue(self.rag.answer("What time is check-in?")["grounded"])


class PdfUploadTests(TempDbTest):
    def test_non_pdf_upload_is_rejected(self):
        with self.assertRaisesRegex(ingest.IngestionError, "not a PDF"):
            ingest.ingest_pdf_bytes(db.DEFAULT_HOTEL_ID, b"#!/bin/sh\nrm -rf /", "evil.sh")

    def test_empty_upload_is_rejected(self):
        with self.assertRaisesRegex(ingest.IngestionError, "No file"):
            ingest.ingest_pdf_bytes(db.DEFAULT_HOTEL_ID, b"", "empty.pdf")

    def test_oversized_upload_is_rejected(self):
        oversized = b"%PDF-" + b"0" * (ingest.MAX_PDF_BYTES + 1)
        with self.assertRaisesRegex(ingest.IngestionError, "larger than"):
            ingest.ingest_pdf_bytes(db.DEFAULT_HOTEL_ID, oversized, "big.pdf")

    def test_pdf_without_extractable_text_is_rejected(self):
        # Valid PDF header but no page text: a scanned image would look like this.
        with self.assertRaises(ingest.IngestionError):
            ingest.ingest_pdf_bytes(db.DEFAULT_HOTEL_ID, b"%PDF-1.4\n%%EOF\n", "scan.pdf")

    def test_a_rejected_upload_leaves_the_existing_index_intact(self):
        ingest.load_index()
        before = ingest.file_digest(ingest.stored_pdf_path(db.DEFAULT_HOTEL_ID))
        with self.assertRaises(ingest.IngestionError):
            ingest.ingest_pdf_bytes(db.DEFAULT_HOTEL_ID, b"not a pdf at all", "bad.pdf")
        self.assertEqual(ingest.file_digest(ingest.stored_pdf_path(db.DEFAULT_HOTEL_ID)), before)
        self.assertTrue(self.hotels and HotelRetriever().answer("What time is check-in?")["grounded"])

    def test_traversal_filenames_are_neutralised(self):
        for nasty in ("../../server.py", "..\\..\\windows\\system32\\cfg.pdf", "/etc/passwd"):
            with self.subTest(nasty=nasty):
                cleaned = ingest.safe_name(nasty)
                self.assertNotIn("/", cleaned)
                self.assertNotIn("\\", cleaned)
                self.assertFalse(cleaned.startswith("."))

    def test_stored_pdf_stays_inside_the_documents_directory(self):
        path = ingest.stored_pdf_path("../../escape")
        self.assertEqual(path.parent.resolve(), ingest.documents_dir().resolve())

    def test_replacing_the_pdf_rebuilds_the_index(self):
        retriever = HotelRetriever()
        self.assertTrue(retriever.answer("Is Wi-Fi free?")["grounded"])
        replacement = build_minimal_pdf([
            "Hotel Rules", "1. Pet Policy",
            "Guests may bring one small dog.",
            "A cleaning fee of INR 2,000 applies per stay."])
        record = ingest.ingest_pdf_bytes(db.DEFAULT_HOTEL_ID, replacement, "rules.pdf")
        self.assertEqual(record["hotel_id"], db.DEFAULT_HOTEL_ID)
        retriever.reload()
        combined = " ".join(c["text"] for c in retriever.sections)
        self.assertIn("dog", combined.lower())
        self.assertNotIn("MeridianGuest", combined)
        # Content that is no longer in the document must now be refused.
        self.assertFalse(retriever.answer("What is the guest Wi-Fi network name?")["grounded"])

    def test_replacement_updates_the_document_record_for_the_hotel(self):
        replacement = build_minimal_pdf([
            "Hotel Rules", "1. Pet Policy", "One small dog is allowed per room."])
        ingest.ingest_pdf_bytes(db.DEFAULT_HOTEL_ID, replacement, "rules.pdf")
        info = ingest.document_info(db.DEFAULT_HOTEL_ID)
        self.assertEqual(info["original_name"], "rules.pdf")
        self.assertEqual(info["hotel_id"], db.DEFAULT_HOTEL_ID)

    def test_each_hotel_has_its_own_document_and_index(self):
        """A retriever is bound to one hotel and never reads another's PDF."""
        other = "HOTEL-TWO"
        with db.connect(self.db) as conn:
            conn.execute("INSERT INTO hotels VALUES (?,?,?,?,?,?,?,?,?)",
                         (other, db.DEFAULT_ORG_ID, "Second Hotel", "1 Other Road",
                          "Pune", "", "", "", "ACTIVE"))
        ingest.ingest_pdf_bytes(other, build_minimal_pdf([
            "Second Hotel", "1. Pool Policy",
            "The rooftop pool is open from 7:00 AM to 8:00 PM."]), "second.pdf")
        first = HotelRetriever(db.DEFAULT_HOTEL_ID)
        second = HotelRetriever(other)
        self.assertIn("MeridianGuest", " ".join(c["text"] for c in first.sections))
        self.assertNotIn("MeridianGuest", " ".join(c["text"] for c in second.sections))
        self.assertIn("rooftop pool", " ".join(c["text"] for c in second.sections).lower())
        self.assertNotEqual(ingest.stored_pdf_path(db.DEFAULT_HOTEL_ID),
                            ingest.stored_pdf_path(other))
        # The second hotel's document does not answer the first hotel's questions.
        self.assertFalse(second.answer("What is the guest Wi-Fi network name?")["grounded"])


# --------------------------------------------------------------------------
# Section 5 — Multi-organization architecture
# --------------------------------------------------------------------------

class MultiOrganizationTest(TempDbTest):
    """Two isolated organizations, each with its own hotels and staff."""

    def setUp(self):
        super().setUp()
        self.orgs = OrganizationStore(self.db)
        self.product_admin = self.auth.register(
            {"name": "Platform", "email": "platform@example.com",
             "password": "password123", "role": "PRODUCT_ADMIN"}, allow_staff=True)
        # Organization A is the seeded default; B is created by the product admin.
        self.org_a = db.DEFAULT_ORG_ID
        self.org_b = self.orgs.create(self.product_admin, {"name": "Coastal Stays"})["id"]

        self.admin_a = self.staff_in("ORGANIZATION_ADMIN", "admin.a@example.com", self.org_a)
        self.admin_b = self.staff_in("ORGANIZATION_ADMIN", "admin.b@example.com", self.org_b)
        self.hotel_a = db.DEFAULT_HOTEL_ID
        self.hotel_b = self.orgs.create_hotel(self.admin_b, {
            "name": "Coastal Retreat", "city": "Goa", "address": "9 Beach Road"})["id"]

    def staff_in(self, role, email, organization_id):
        return self.auth.register(
            {"name": role.title(), "email": email, "password": "password123", "role": role},
            allow_staff=True, organization_id=organization_id)


class ProductAdminTests(MultiOrganizationTest):
    def test_product_admin_can_create_and_list_every_organization(self):
        listed = {o["id"] for o in self.orgs.list(self.product_admin)}
        self.assertIn(self.org_a, listed)
        self.assertIn(self.org_b, listed)

    def test_product_admin_can_update_an_organization(self):
        updated = self.orgs.update(self.product_admin, self.org_b, {"name": "Coastal Stays Group"})
        self.assertEqual(updated["name"], "Coastal Stays Group")

    def test_product_admin_can_deactivate_an_organization(self):
        updated = self.orgs.update(self.product_admin, self.org_b, {"status": "INACTIVE"})
        self.assertEqual(updated["status"], "INACTIVE")
        self.assertNotIn(self.org_b, [o["id"] for o in self.orgs.list_public()])

    def test_organization_name_and_status_are_validated(self):
        with self.assertRaises(ValueError):
            self.orgs.create(self.product_admin, {"name": "   "})
        with self.assertRaises(ValueError):
            self.orgs.update(self.product_admin, self.org_b, {"status": "PAUSED"})

    def test_product_admin_sees_hotels_across_organizations(self):
        ids = {h["id"] for h in self.orgs.list_hotels(self.product_admin)}
        self.assertIn(self.hotel_a, ids)
        self.assertIn(self.hotel_b, ids)


class OrganizationAdminTests(MultiOrganizationTest):
    def test_organization_admin_cannot_create_organizations(self):
        with self.assertRaises(PermissionError_):
            self.orgs.create(self.admin_a, {"name": "Sneaky Org"})

    def test_organization_admin_cannot_update_another_organization(self):
        with self.assertRaises(PermissionError_):
            self.orgs.update(self.admin_a, self.org_b, {"name": "Hijacked"})

    def test_organization_admin_only_lists_its_own_organization(self):
        self.assertEqual([o["id"] for o in self.orgs.list(self.admin_a)], [self.org_a])
        self.assertEqual([o["id"] for o in self.orgs.list(self.admin_b)], [self.org_b])

    def test_organization_admin_cannot_read_another_organization(self):
        with self.assertRaises(PermissionError_):
            self.orgs.get(self.admin_a, self.org_b)

    def test_organization_admin_only_sees_its_own_hotels(self):
        self.assertEqual([h["id"] for h in self.orgs.list_hotels(self.admin_a)], [self.hotel_a])
        self.assertEqual([h["id"] for h in self.orgs.list_hotels(self.admin_b)], [self.hotel_b])

    def test_organization_admin_cannot_read_another_organizations_hotel(self):
        with self.assertRaises(PermissionError_):
            self.orgs.get_hotel(self.admin_a, self.hotel_b)

    def test_organization_admin_cannot_act_on_another_organizations_hotel(self):
        with self.assertRaises(PermissionError_):
            self.orgs.require_hotel_access(self.admin_a, self.hotel_b)
        self.orgs.require_hotel_access(self.admin_b, self.hotel_b)

    def test_an_organization_can_hold_multiple_hotels(self):
        second = self.orgs.create_hotel(self.admin_b, {
            "name": "Coastal Annexe", "city": "Goa", "address": "11 Beach Road"})
        ids = [h["id"] for h in self.orgs.list_hotels(self.admin_b)]
        self.assertIn(self.hotel_b, ids)
        self.assertIn(second["id"], ids)
        self.assertEqual(len(ids), 2)

    def test_hotel_fields_are_validated(self):
        with self.assertRaises(ValueError):
            self.orgs.create_hotel(self.admin_b, {"name": "", "city": "Goa", "address": "x"})

    def test_a_new_hotel_belongs_to_the_creating_organization(self):
        hotel = self.orgs.get_hotel(self.admin_b, self.hotel_b)
        self.assertEqual(hotel["organization_id"], self.org_b)


class ReceptionistAssignmentTests(MultiOrganizationTest):
    def setUp(self):
        super().setUp()
        self.second_b = self.orgs.create_hotel(self.admin_b, {
            "name": "Coastal Annexe", "city": "Goa", "address": "11 Beach Road"})["id"]
        self.reception_b = self.staff_in("RECEPTIONIST", "reception.b@example.com", self.org_b)

    def test_receptionist_starts_with_no_hotels(self):
        self.assertEqual(self.orgs.assigned_hotel_ids(self.reception_b["id"]), [])
        self.assertEqual(self.orgs.list_hotels(self.reception_b), [])

    def test_admin_can_assign_a_receptionist_to_a_hotel(self):
        assigned = self.orgs.assign_receptionist(self.admin_b, self.reception_b["id"], self.hotel_b)
        self.assertEqual(assigned, [self.hotel_b])
        self.assertEqual([h["id"] for h in self.orgs.list_hotels(self.reception_b)], [self.hotel_b])

    def test_receptionist_is_limited_to_assigned_hotels(self):
        self.orgs.assign_receptionist(self.admin_b, self.reception_b["id"], self.hotel_b)
        self.orgs.require_hotel_access(self.reception_b, self.hotel_b)
        with self.assertRaises(PermissionError_):
            self.orgs.require_hotel_access(self.reception_b, self.second_b)
        with self.assertRaises(PermissionError_):
            self.orgs.get_hotel(self.reception_b, self.second_b)

    def test_assignment_can_be_removed(self):
        self.orgs.assign_receptionist(self.admin_b, self.reception_b["id"], self.hotel_b)
        self.assertEqual(self.orgs.unassign_receptionist(
            self.admin_b, self.reception_b["id"], self.hotel_b), [])
        with self.assertRaises(PermissionError_):
            self.orgs.require_hotel_access(self.reception_b, self.hotel_b)

    def test_admin_cannot_assign_into_another_organization(self):
        with self.assertRaises(PermissionError_):
            self.orgs.assign_receptionist(self.admin_a, self.reception_b["id"], self.hotel_b)

    def test_receptionist_cannot_be_assigned_across_organizations(self):
        reception_a = self.staff_in("RECEPTIONIST", "reception.a@example.com", self.org_a)
        with self.assertRaises(PermissionError_):
            self.orgs.assign_receptionist(self.product_admin, reception_a["id"], self.hotel_b)

    def test_only_receptionists_can_be_assigned(self):
        with self.assertRaises(ValueError):
            self.orgs.assign_receptionist(self.admin_b, self.admin_b["id"], self.hotel_b)

    def test_a_receptionist_cannot_assign_itself(self):
        with self.assertRaises(PermissionError_):
            self.orgs.assign_receptionist(self.reception_b, self.reception_b["id"], self.second_b)

    def test_unknown_hotel_or_user_is_rejected(self):
        with self.assertRaises(LookupError):
            self.orgs.assign_receptionist(self.admin_b, self.reception_b["id"], "NO-SUCH-HOTEL")
        with self.assertRaises(LookupError):
            self.orgs.assign_receptionist(self.admin_b, "USR-NOPE", self.hotel_b)


class CustomerHotelSelectionTests(MultiOrganizationTest):
    def setUp(self):
        super().setUp()
        self.customer = self.customer()
        self.hotels.create_room({"room_number": "B1", "room_type": "Sea View Suite",
                                 "capacity": 2, "price_per_night": 9000,
                                 "description": "Balcony", "amenities": "Wi-Fi"},
                                hotel_id=self.hotel_b)

    def test_customer_browses_organizations_then_hotels(self):
        organizations = {o["id"] for o in self.orgs.list_public()}
        self.assertIn(self.org_a, organizations)
        self.assertIn(self.org_b, organizations)
        hotels_b = [h["id"] for h in self.orgs.list_hotels(organization_id=self.org_b)]
        self.assertEqual(hotels_b, [self.hotel_b])

    def test_availability_is_scoped_to_the_selected_hotel(self):
        check_in, check_out = self.dates()
        rooms_a = self.bookings.search(check_in, check_out, 2, hotel_id=self.hotel_a)
        rooms_b = self.bookings.search(check_in, check_out, 2, hotel_id=self.hotel_b)
        self.assertEqual(len(rooms_b), 1)
        self.assertEqual(rooms_b[0]["room_number"], "B1")
        self.assertNotIn("B1", [r["room_number"] for r in rooms_a])

    def test_customer_can_book_in_the_selected_hotel(self):
        check_in, check_out = self.dates()
        room = self.bookings.search(check_in, check_out, 2, hotel_id=self.hotel_b)[0]
        booking = self.bookings.create(self.customer, {
            "room_id": room["room_id"], "check_in": check_in,
            "check_out": check_out, "guests": 2})
        self.assertEqual(booking["hotel_id"], self.hotel_b)
        self.assertEqual(booking["organization_id"], self.org_b)
        self.assertEqual(booking["total_amount"], 18000)


class CrossOrganizationIsolationTests(MultiOrganizationTest):
    def setUp(self):
        super().setUp()
        self.customer = self.customer()
        self.hotels.create_room({"room_number": "B1", "room_type": "Sea View Suite",
                                 "capacity": 2, "price_per_night": 9000},
                                hotel_id=self.hotel_b)
        check_in, check_out = self.dates()
        room_a = self.bookings.search(check_in, check_out, 2, hotel_id=self.hotel_a)[0]
        room_b = self.bookings.search(check_in, check_out, 2, hotel_id=self.hotel_b)[0]
        self.booking_a = self.bookings.create(self.customer, {
            "room_id": room_a["room_id"], "check_in": check_in,
            "check_out": check_out, "guests": 2})
        self.booking_b = self.bookings.create(self.customer, {
            "room_id": room_b["room_id"], "check_in": check_in,
            "check_out": check_out, "guests": 2})

    def test_staff_booking_lists_are_scoped_to_their_organization(self):
        ids_a = [b["id"] for b in self.bookings.list_all(
            hotel_ids=self.orgs.accessible_hotel_ids(self.admin_a))]
        ids_b = [b["id"] for b in self.bookings.list_all(
            hotel_ids=self.orgs.accessible_hotel_ids(self.admin_b))]
        self.assertEqual(ids_a, [self.booking_a["id"]])
        self.assertEqual(ids_b, [self.booking_b["id"]])

    def test_product_admin_sees_bookings_from_every_organization(self):
        self.assertIsNone(self.orgs.accessible_hotel_ids(self.product_admin))
        ids = [b["id"] for b in self.bookings.list_all()]
        self.assertIn(self.booking_a["id"], ids)
        self.assertIn(self.booking_b["id"], ids)

    def test_unassigned_receptionist_sees_no_bookings(self):
        receptionist = self.staff_in("RECEPTIONIST", "r.b@example.com", self.org_b)
        self.assertEqual(self.bookings.list_all(
            hotel_ids=self.orgs.accessible_hotel_ids(receptionist)), [])

    def test_assigned_receptionist_sees_only_its_hotel(self):
        receptionist = self.staff_in("RECEPTIONIST", "r.b@example.com", self.org_b)
        self.orgs.assign_receptionist(self.admin_b, receptionist["id"], self.hotel_b)
        ids = [b["id"] for b in self.bookings.list_all(
            hotel_ids=self.orgs.accessible_hotel_ids(receptionist))]
        self.assertEqual(ids, [self.booking_b["id"]])

    def test_cancellation_queue_is_scoped_by_organization(self):
        for booking in (self.booking_a, self.booking_b):
            with db.connect(self.db) as conn:
                conn.execute("UPDATE bookings SET status='CANCELLATION_REQUESTED' WHERE id=?",
                             (booking["id"],))
        ids_a = [b["id"] for b in self.bookings.list_cancellation_requests(
            hotel_ids=self.orgs.accessible_hotel_ids(self.admin_a))]
        self.assertEqual(ids_a, [self.booking_a["id"]])

    def test_a_customer_still_sees_bookings_across_organizations(self):
        """Customers are platform-wide; the isolation rules apply to staff."""
        ids = {b["id"] for b in self.bookings.list_for_customer(self.customer["id"])}
        self.assertEqual(ids, {self.booking_a["id"], self.booking_b["id"]})

    def test_each_hotel_keeps_its_own_room_inventory(self):
        rooms_a = {r["room_number"] for r in self.hotels.list_rooms(self.hotel_a)}
        rooms_b = {r["room_number"] for r in self.hotels.list_rooms(self.hotel_b)}
        self.assertIn("201", rooms_a)
        self.assertEqual(rooms_b, {"B1"})
        self.assertFalse(rooms_a & rooms_b)

    def test_room_lookup_can_be_constrained_to_one_hotel(self):
        room_b = self.hotels.list_rooms(self.hotel_b)[0]
        self.assertIsNotNone(self.hotels.get_room(room_b["id"], hotel_id=self.hotel_b))
        self.assertIsNone(self.hotels.get_room(room_b["id"], hotel_id=self.hotel_a))


if __name__ == "__main__":
    unittest.main()
