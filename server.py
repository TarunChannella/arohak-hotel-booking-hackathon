"""HTTP API and static file server.

Every route below is authorized server-side. The client is never trusted to
declare its own role: the role is read from the session token on each request.
"""
import json
import mimetypes
import os
import re
from datetime import UTC, datetime
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
from organization import OrganizationStore
from rag import HotelRetriever

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
COOKIE_NAME = "session"

# The stores create the schema, so they must exist before the retriever, which
# records the ingested document against the hotel.
auth_store = AuthStore()
hotels = HotelStore()
bookings = BookingStore()
organizations = OrganizationStore()
retriever = HotelRetriever()
# One retriever per hotel, built on first use. Each reads only its own PDF.
_retrievers = {retriever.hotel_id: retriever}


def get_retriever(hotel_id):
    """Return the retriever for one hotel, building it on first use."""
    if hotel_id not in _retrievers:
        _retrievers[hotel_id] = HotelRetriever(hotel_id)
    return _retrievers[hotel_id]


def drop_retriever(hotel_id):
    _retrievers.pop(hotel_id, None)
# The assistant reaches application data only through this tool layer.
assistant = BookingAssistant(ToolLayer(bookings, hotels))

ORG_ID_RE = re.compile(r"^/api/organizations/([A-Za-z0-9\-]+)$")
HOTEL_ID_RE = re.compile(r"^/api/hotels/([A-Za-z0-9\-]+)(/[a-z\-]+)?$")
ROOM_ID_RE = re.compile(r"^/api/rooms/([A-Za-z0-9\-]+)(/[a-z\-]+)?$")


def resolve_chat_hotel(user, hotel_id):
    """Validate the hotel a chat or assistant request names.

    Customers may use any active hotel; staff are held to their own scope. An
    unknown, inactive or out-of-scope hotel is refused rather than quietly
    falling back to the default.
    """
    if not hotel_id:
        return hotels.get_hotel(db.DEFAULT_HOTEL_ID)
    hotel = hotels.get_hotel(hotel_id)
    if not hotel:
        raise LookupError("Hotel not found.")
    if auth.is_staff(user):
        organizations.require_hotel_access(user, hotel_id)
    elif hotel["status"] != "ACTIVE":
        raise LookupError("Hotel not found.")
    return hotel


def scoped_hotel_ids(user):
    """Hotel ids a staff user may see. None means no restriction."""
    return organizations.accessible_hotel_ids(user)
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

            if route == "/api/organizations":
                user = self.current_user()
                # Staff see the organizations they administer; customers and
                # visitors browse every active organization on the platform.
                if user and auth.is_staff(user):
                    return self.send_json(200, {"organizations": organizations.list(user)})
                return self.send_json(200, {"organizations": organizations.list_public()})

            if route == "/api/hotels":
                user = self.current_user()
                return self.send_json(200, {"hotels": organizations.list_hotels(
                    user, organization_id=query.get("organization_id", [None])[0])})

            org_match = ORG_ID_RE.match(route)
            if org_match:
                organization = organizations.get(require_user(self.current_user()), org_match.group(1))
                if not organization:
                    return self.send_json(404, {"error": "Organization not found."})
                return self.send_json(200, {"organization": organization})

            hotel_match = HOTEL_ID_RE.match(route)
            if hotel_match and hotel_match.group(2) == "/rooms":
                user = self.current_user()
                return self.send_json(200, {"rooms": hotels.list_rooms(
                    hotel_match.group(1), include_inactive=auth.is_staff(user))})
            if hotel_match and not hotel_match.group(2):
                hotel = organizations.get_hotel(self.current_user(), hotel_match.group(1))
                if not hotel:
                    return self.send_json(404, {"error": "Hotel not found."})
                return self.send_json(200, {"hotel": hotel})

            if route == "/api/hotel":
                return self.send_json(200, {"hotel": hotels.get_hotel()})

            if route == "/api/hotel/document":
                user = self.current_user()
                hotel = resolve_chat_hotel(user, query.get("hotel_id", [None])[0])
                return self.send_json(200, {"document": ingest.document_info(hotel["id"]),
                                            "hotel": {"id": hotel["id"], "name": hotel["name"]}})

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
                    hotel_id=query.get("hotel_id", [db.DEFAULT_HOTEL_ID])[0] or db.DEFAULT_HOTEL_ID,
                )
                return self.send_json(200, {"rooms": rooms})

            if route == "/api/bookings":
                user = require_user(self.current_user())
                bookings.mark_completed()
                if auth.is_staff(user):
                    return self.send_json(200, {"bookings": bookings.list_all(
                        status=query.get("status", [None])[0],
                        query=query.get("q", [None])[0],
                        hotel_ids=scoped_hotel_ids(user))})
                return self.send_json(200, {"bookings": bookings.list_for_customer(user["id"])})

            if route == "/api/cancellations":
                user = require_role(self.current_user(), *auth.STAFF_ROLES)
                return self.send_json(200, {"bookings": bookings.list_cancellation_requests(
                    hotel_ids=scoped_hotel_ids(user))})

            if route == "/api/users":
                user = require_role(self.current_user(), "PRODUCT_ADMIN", "ORGANIZATION_ADMIN", "ADMIN")
                wanted = query.get("role", [None])[0]
                people = auth_store.list_users()
                if user["role"] != "PRODUCT_ADMIN":
                    people = [p for p in people if p["organization_id"] == user["organization_id"]]
                if wanted:
                    people = [p for p in people if p["role"] == wanted]
                for person in people:
                    if person["role"] == "RECEPTIONIST":
                        person["hotel_ids"] = organizations.assigned_hotel_ids(person["id"])
                return self.send_json(200, {"users": people})

            match = BOOKING_ID_RE.match(route)
            if match and not match.group(2):
                user = require_user(self.current_user())
                booking = bookings.get(match.group(1))
                if not booking:
                    return self.send_json(404, {"error": "Booking not found."})
                if auth.is_staff(user):
                    allowed = scoped_hotel_ids(user)
                    if allowed is not None and booking["hotel_id"] not in allowed:
                        return self.send_json(404, {"error": "Booking not found."})
                elif booking["customer_id"] != user["id"]:
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
                user = require_role(self.current_user(), "PRODUCT_ADMIN", "ORGANIZATION_ADMIN", "ADMIN")
                hotel_id = self.headers.get("X-Hotel-Id", db.DEFAULT_HOTEL_ID)
                organizations.require_hotel_access(user, hotel_id)
                data = self.read_body(ingest.MAX_PDF_BYTES + 1024)
                name = self.headers.get("X-Filename", "document.pdf")
                record = ingest.ingest_pdf_bytes(hotel_id, data, name)
                # Rebuild only this hotel's index; every other hotel is untouched.
                if hotel_id in _retrievers:
                    chunks = _retrievers[hotel_id].reload()
                else:
                    drop_retriever(hotel_id)
                    chunks = record["chunk_count"]
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
                actor = require_role(self.current_user(), "PRODUCT_ADMIN", "ORGANIZATION_ADMIN", "ADMIN")
                # An organization admin may only create staff inside its own
                # organization; a product admin may target any organization.
                target_org = payload.get("organization_id") or actor["organization_id"]
                if actor["role"] != "PRODUCT_ADMIN" and target_org != actor["organization_id"]:
                    raise PermissionError_("That organization is outside your access.")
                return self.send_json(201, {"user": auth_store.register(
                    payload, allow_staff=True, organization_id=target_org)})

            if route == "/api/organizations":
                return self.send_json(201, {"organization": organizations.create(
                    require_user(self.current_user()), payload)})

            if route == "/api/hotels":
                return self.send_json(201, {"hotel": organizations.create_hotel(
                    require_user(self.current_user()), payload)})

            if route == "/api/assignments":
                user = require_user(self.current_user())
                assigned = organizations.assign_receptionist(
                    user, str(payload.get("user_id", "")), str(payload.get("hotel_id", "")))
                return self.send_json(201, {"hotel_ids": assigned})

            if route == "/api/assignments/remove":
                user = require_user(self.current_user())
                assigned = organizations.unassign_receptionist(
                    user, str(payload.get("user_id", "")), str(payload.get("hotel_id", "")))
                return self.send_json(200, {"hotel_ids": assigned})

            if route == "/api/assistant":
                user = require_role(self.current_user(), "CUSTOMER")
                message = str(payload.get("message", "")).strip()
                hotel = resolve_chat_hotel(user, payload.get("hotel_id"))
                return self.send_json(200, assistant.respond(user, message, hotel_id=hotel["id"]))

            if route == "/api/chat":
                question = str(payload.get("question", "")).strip()
                if not question:
                    raise ValueError("Please enter a question.")
                hotel = resolve_chat_hotel(self.current_user(), payload.get("hotel_id"))
                try:
                    answer = get_retriever(hotel["id"]).answer(question)
                except ingest.MissingDocument:
                    return self.send_json(200, {
                        "answer": f"No hotel information document has been uploaded for "
                                  f"{hotel['name']} yet, so I cannot answer questions about it.",
                        "grounded": False, "citations": [], "hotel": hotel["name"]})
                answer["hotel"] = hotel["name"]
                for citation in answer["citations"]:
                    citation["hotel"] = hotel["name"]
                return self.send_json(200, answer)

            if route == "/api/rooms":
                user = require_role(self.current_user(), "PRODUCT_ADMIN", "ORGANIZATION_ADMIN", "ADMIN")
                hotel_id = str(payload.get("hotel_id") or db.DEFAULT_HOTEL_ID)
                organizations.require_hotel_access(user, hotel_id)
                return self.send_json(201, {"room": hotels.create_room(payload, hotel_id)})

            if route == "/api/bookings":
                user = require_role(self.current_user(), "CUSTOMER")
                return self.send_json(201, {"booking": bookings.create(user, payload)})

            match = ROOM_ID_RE.match(route)
            if match and match.group(2) in ("/activate", "/deactivate"):
                user = require_role(self.current_user(), "PRODUCT_ADMIN", "ORGANIZATION_ADMIN", "ADMIN")
                existing = hotels.get_room(match.group(1))
                if not existing:
                    raise LookupError("Room not found.")
                organizations.require_hotel_access(user, existing["hotel_id"])
                status = "ACTIVE" if match.group(2) == "/activate" else "INACTIVE"
                return self.send_json(200, {"room": hotels.set_room_status(match.group(1), status)})

            match = BOOKING_ID_RE.match(route)
            if match and match.group(2) == "/cancel":
                user = require_user(self.current_user())
                # Staff cancel on a customer's behalf; customers only their own.
                owner = None if auth.is_staff(user) else user["id"]
                return self.send_json(200, {"booking": bookings.cancel(match.group(1), owner)})

            if match and match.group(2) in ("/approve-cancellation", "/reject-cancellation"):
                user = require_role(self.current_user(), *auth.STAFF_ROLES)
                target = bookings.get(match.group(1))
                if not target:
                    raise LookupError("Booking not found.")
                organizations.require_hotel_access(user, target["hotel_id"])
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
                user = require_role(self.current_user(), "PRODUCT_ADMIN", "ORGANIZATION_ADMIN", "ADMIN")
                hotel_id = str(payload.pop("hotel_id", None) or db.DEFAULT_HOTEL_ID)
                organizations.require_hotel_access(user, hotel_id)
                return self.send_json(200, {"hotel": hotels.update_hotel(payload, hotel_id)})

            org_match = ORG_ID_RE.match(route)
            if org_match:
                return self.send_json(200, {"organization": organizations.update(
                    require_user(self.current_user()), org_match.group(1), payload)})

            hotel_match = HOTEL_ID_RE.match(route)
            if hotel_match and not hotel_match.group(2):
                user = require_role(self.current_user(), "PRODUCT_ADMIN", "ORGANIZATION_ADMIN", "ADMIN")
                organizations.require_hotel_access(user, hotel_match.group(1))
                return self.send_json(200, {"hotel": hotels.update_hotel(payload, hotel_match.group(1))})

            match = ROOM_ID_RE.match(route)
            if match and not match.group(2):
                # Receptionists may maintain room details and availability but
                # may not create or remove rooms, and may not edit the hotel.
                user = require_role(self.current_user(), *auth.STAFF_ROLES)
                existing = hotels.get_room(match.group(1))
                if not existing:
                    raise LookupError("Room not found.")
                organizations.require_hotel_access(user, existing["hotel_id"])
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
            user = auth_store.register(
                {"name": name, "email": email, "password": password, "role": role},
                allow_staff=True)
            created.append(f"{role}: {email}")
            if role == "RECEPTIONIST":
                # A receptionist only sees hotels assigned to it, so the demo
                # account needs the seeded hotel or it would see nothing.
                with db.connect(db.database_path()) as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO receptionist_hotels VALUES (?,?,?)",
                        (user["id"], db.DEFAULT_HOTEL_ID,
                         datetime.now(UTC).isoformat(timespec="seconds")))
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
