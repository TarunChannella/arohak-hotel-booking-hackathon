import os
import tempfile
import unittest
from datetime import date, timedelta

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from booking import BookingStore
from rag import HotelRetriever


class RagTests(unittest.TestCase):
    def setUp(self): self.rag = HotelRetriever()

    def test_grounded_checkin(self):
        result = self.rag.answer("What time is check in?")
        self.assertTrue(result["grounded"])
        self.assertIn("2:00 PM", result["answer"])
        self.assertTrue(result["citations"])

    def test_unknown_is_refused(self):
        result = self.rag.answer("Does the hotel have a casino?")
        self.assertFalse(result["grounded"])
        self.assertIn("not available", result["answer"])

    def test_four_guests_retrieves_rooms(self):
        result = self.rag.answer("Which room is suitable for four guests?")
        self.assertIn("Family Suite", result["answer"])

    def test_late_checkout_charge(self):
        result = self.rag.answer("What happens if I check out at 4 PM?")
        self.assertIn("50%", result["answer"])
        self.assertTrue(result["grounded"])


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close(); self.store = BookingStore(self.tmp.name)

    def tearDown(self): os.unlink(self.tmp.name)

    def payload(self):
        start = date.today() + timedelta(days=5)
        return {"guest_name":"Tarun", "email":"tarun@example.com", "room_type":"Deluxe King", "guests":2,
                "check_in":start.isoformat(), "check_out":(start+timedelta(days=2)).isoformat()}

    def test_create_and_list(self):
        booking = self.store.create(self.payload())
        self.assertEqual(booking["status"], "CONFIRMED")
        self.assertEqual(booking["total"], 17000)
        self.assertEqual(len(self.store.list("tarun@example.com")), 1)

    def test_capacity_is_enforced(self):
        payload = self.payload(); payload["guests"] = 4
        with self.assertRaisesRegex(ValueError, "allows"):
            self.store.create(payload)

    def test_direct_cancellation(self):
        booking = self.store.create(self.payload())
        cancelled = self.store.cancel(booking["id"])
        self.assertEqual(cancelled["status"], "CANCELLED")


if __name__ == "__main__": unittest.main()
