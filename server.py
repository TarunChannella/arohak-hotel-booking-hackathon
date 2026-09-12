"""HTTP API and static file server.

Every route below is authorized server-side. The client is never trusted to
declare its own role: the role is read from the session token on each request.
"""
import json
import mimetypes
import os
import re
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import auth
import db
import ingest
from assistant import BookingAssistant, ToolLayer
from auth import AuthError, AuthStore, PermissionError_, require_role, require_user
from booking import BookingStore
from hotel import HotelStore
from ingest import IngestionError
from rag import HotelRetriever

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
COOKIE_NAME = "session"

# The stores create the schema, so they must exist before the retriever, which
# records the ingested document against the hotel.
auth_store = AuthStore()
hotels = HotelStore()
bookings = BookingStore()
retriever = HotelRetriever()
# The assistant reaches application data only through this tool layer.
assistant = BookingAssistant(ToolLayer(bookings, hotels))

ROOM_ID_RE = re.compile(r"^/api/rooms/([A-Za-z0-9\-]+)(/[a-z\-]+)?$")
BOOKING_ID_RE = re.compile(r"^/api/bookings/([A-Za-z0-9\-]+)(/[a-z\-]+)?$")


class Handler(BaseHTTPRequestHandler):
    server_version = "MeridianBooking/1.0"

    # ---------- plumbing ----------

    def send_json(self, status, payload, cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("Request body is too large.")
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            raise ValueError("Request body must be valid JSON.")

    def token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            return SimpleCookie(raw).get(COOKIE_NAME).value
        except (AttributeError, KeyError):
            return None

    def current_user(self):
        return auth_store.user_for_token(self.token())

    @staticmethod
    def session_cookie(token, clear=False):
        if clear:
            return f"{COOKIE_NAME}=; HttpOnly; Path=/; SameSite=Strict; Max-Age=0"
        return (f"{COOKIE_NAME}={token}; HttpOnly; Path=/; SameSite=Strict; "
                f"Max-Age={auth.SESSION_HOURS * 3600}")

    # ---------- GET ----------

    def do_GET(self):
        parsed = urlparse(self.path)
        route, query = parsed.path, parse_qs(parsed.query)
        try:
            if route == "/api/health":
                return self.send_json(200, {"status": "ok"})

            if route == "/api/auth/me":
                user = self.current_user()
                return self.send_json(200, {"user": user})

            if route == "/api/hotel":
                return self.send_json(200, {"hotel": hotels.get_hotel()})

            if route == "/api/hotel/document":
                return self.send_json(200, {"document": ingest.document_info(db.DEFAULT_HOTEL_ID)})

            if route == "/api/rooms":
                user = self.current_user()
                # Staff see every room including deactivated ones; guests and
                # customers only see rooms that are active.
                return self.send_json(200, {"rooms": hotels.list_rooms(include_inactive=auth.is_staff(user))})

            if route == "/api/availability":
                rooms = bookings.search(
                    query.get("check_in", [""])[0],
                    query.get("check_out", [""])[0],
                    int(query.get("guests", ["1"])[0] or 1),
                )
                return self.send_json(200, {"rooms": rooms})

            if route == "/api/bookings":
                user = require_user(self.current_user())
                bookings.mark_completed()
                if auth.is_staff(user):
                    return self.send_json(200, {"bookings": bookings.list_all(
                        status=query.get("status", [None])[0],
                        query=query.get("q", [None])[0])})
                return self.send_json(200, {"bookings": bookings.list_for_customer(user["id"])})

            if route == "/api/cancellations":
                require_role(self.current_user(), "ADMIN", "RECEPTIONIST")
                return self.send_json(200, {"bookings": bookings.list_cancellation_requests()})

            if route == "/api/users":
                require_role(self.current_user(), "ADMIN")
                return self.send_json(200, {"users": auth_store.list_users()})

            match = BOOKING_ID_RE.match(route)
            if match and not match.group(2):
                user = require_user(self.current_user())
                booking = bookings.get(match.group(1))
                if not booking or (not auth.is_staff(user) and booking["customer_id"] != user["id"]):
                    return self.send_json(404, {"error": "Booking not found."})
                return self.send_json(200, {"booking": booking})

            return self.serve_static(route)
        except PermissionError_ as exc:
            return self.send_json(401 if not self.current_user() else 403, {"error": str(exc)})
        except LookupError as exc:
            return self.send_json(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(500, {"error": "Unexpected server error."})

    def serve_static(self, route):
        path = STATIC / ("index.html" if route == "/" else route.lstrip("/"))
        try:
            if not path.resolve().is_relative_to(STATIC.resolve()) or not path.is_file():
                raise FileNotFoundError
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (FileNotFoundError, OSError):
            self.send_error(404)

    # ---------- POST ----------

    def read_body(self, limit):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("No file was supplied.")
        if length > limit:
            raise ValueError(f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        return self.rfile.read(length)

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/hotel/document":
                # Raw PDF body, so no multipart parser is needed.
                require_role(self.current_user(), "ADMIN")
                data = self.read_body(ingest.MAX_PDF_BYTES + 1024)
                name = self.headers.get("X-Filename", "document.pdf")
                record = ingest.ingest_pdf_bytes(db.DEFAULT_HOTEL_ID, data, name)
                chunks = retriever.reload()
                return self.send_json(201, {"document": {
                    "hotel_id": record["hotel_id"], "original_name": record["original_name"],
                    "pages": record["pages"], "chunk_count": record["chunk_count"],
                    "uploaded_at": record["generated_at"]}, "indexed_chunks": chunks})

            payload = self.read_json()

            if route == "/api/auth/register":
                user = auth_store.register(payload)
                token = auth_store.create_session(user["id"])
                return self.send_json(201, {"user": user}, cookie=self.session_cookie(token))

            if route == "/api/auth/login":
                token, user = auth_store.login(payload.get("email"), payload.get("password"))
                return self.send_json(200, {"user": user}, cookie=self.session_cookie(token))

            if route == "/api/auth/logout":
                auth_store.logout(self.token())
                return self.send_json(200, {"ok": True}, cookie=self.session_cookie(None, clear=True))

            if route == "/api/staff":
                require_role(self.current_user(), "ADMIN")
                return self.send_json(201, {"user": auth_store.register(payload, allow_staff=True)})

            if route == "/api/assistant":
                user = require_role(self.current_user(), "CUSTOMER")
                message = str(payload.get("message", "")).strip()
                return self.send_json(200, assistant.respond(user, message))

            if route == "/api/chat":
                question = str(payload.get("question", "")).strip()
                if not question:
                    raise ValueError("Please enter a question.")
                return self.send_json(200, retriever.answer(question))

            if route == "/api/rooms":
                require_role(self.current_user(), "ADMIN")
                return self.send_json(201, {"room": hotels.create_room(payload)})

            if route == "/api/bookings":
                user = require_role(self.current_user(), "CUSTOMER")
                return self.send_json(201, {"booking": bookings.create(user, payload)})

            match = ROOM_ID_RE.match(route)
            if match and match.group(2) in ("/activate", "/deactivate"):
                require_role(self.current_user(), "ADMIN")
                status = "ACTIVE" if match.group(2) == "/activate" else "INACTIVE"
                return self.send_json(200, {"room": hotels.set_room_status(match.group(1), status)})

            match = BOOKING_ID_RE.match(route)
            if match and match.group(2) == "/cancel":
                user = require_user(self.current_user())
                # Staff cancel on a customer's behalf; customers only their own.
                owner = None if auth.is_staff(user) else user["id"]
                return self.send_json(200, {"booking": bookings.cancel(match.group(1), owner)})

            if match and match.group(2) in ("/approve-cancellation", "/reject-cancellation"):
                require_role(self.current_user(), "ADMIN", "RECEPTIONIST")
                approve = match.group(2) == "/approve-cancellation"
                return self.send_json(200, {"booking": bookings.review_cancellation(match.group(1), approve)})

            return self.send_json(404, {"error": "Endpoint not found."})
        except (AuthError, IngestionError) as exc:
            return self.send_json(400, {"error": str(exc)})
        except PermissionError_ as exc:
            return self.send_json(401 if not self.current_user() else 403, {"error": str(exc)})
        except LookupError as exc:
            return self.send_json(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(500, {"error": "Unexpected server error."})

    # ---------- PUT ----------

    def do_PUT(self):
        route = urlparse(self.path).path
        try:
            payload = self.read_json()

            if route == "/api/hotel":
                require_role(self.current_user(), "ADMIN")
                return self.send_json(200, {"hotel": hotels.update_hotel(payload)})

            match = ROOM_ID_RE.match(route)
            if match and not match.group(2):
                # Receptionists may maintain room details and availability but
                # may not create or remove rooms, and may not edit the hotel.
                require_role(self.current_user(), "ADMIN", "RECEPTIONIST")
                return self.send_json(200, {"room": hotels.update_room(match.group(1), payload)})

            return self.send_json(404, {"error": "Endpoint not found."})
        except PermissionError_ as exc:
            return self.send_json(401 if not self.current_user() else 403, {"error": str(exc)})
        except LookupError as exc:
            return self.send_json(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(500, {"error": "Unexpected server error."})

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def seed_demo_accounts():
    """Create demo logins on first run so the app is usable immediately."""
    demo = [
        ("Priya Nair", "admin@meridiangrand.example", "ADMIN"),
        ("Rahul Desai", "reception@meridiangrand.example", "RECEPTIONIST"),
        ("Ananya Rao", "customer@example.com", "CUSTOMER"),
    ]
    password = os.environ.get("DEMO_PASSWORD", "Hackathon2026")
    created = []
    for name, email, role in demo:
        try:
            auth_store.register({"name": name, "email": email, "password": password, "role": role},
                                allow_staff=True)
            created.append(f"{role}: {email}")
        except AuthError:
            pass  # already present
    return created


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    new_accounts = seed_demo_accounts()
    if new_accounts:
        print("Seeded demo accounts (password from DEMO_PASSWORD env var, default in README):")
        for line in new_accounts:
            print("  " + line)
    print(f"Meridian Grand booking system running at http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
