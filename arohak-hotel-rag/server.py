import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from booking import BookingStore
from rag import HotelRetriever

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
retriever = HotelRetriever()
store = BookingStore()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            raise ValueError("Request body must be valid JSON.")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self.send_json(200, {"status": "ok"})
        if parsed.path == "/api/availability":
            query = parse_qs(parsed.query)
            try:
                rooms = store.availability(query.get("check_in", [""])[0], query.get("check_out", [""])[0], int(query.get("guests", [1])[0]))
                return self.send_json(200, {"rooms": rooms})
            except (ValueError, TypeError) as exc:
                return self.send_json(400, {"error": str(exc)})
        if parsed.path == "/api/bookings":
            email = parse_qs(parsed.query).get("email", [""])[0]
            if not email:
                return self.send_json(400, {"error": "Email is required."})
            return self.send_json(200, {"bookings": store.list(email)})
        path = STATIC / ("index.html" if parsed.path == "/" else parsed.path.lstrip("/"))
        try:
            if not path.resolve().is_relative_to(STATIC.resolve()) or not path.is_file():
                raise FileNotFoundError
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def do_POST(self):
        try:
            payload = self.read_json()
            if self.path == "/api/chat":
                question = str(payload.get("question", "")).strip()
                if not question:
                    raise ValueError("Please enter a question.")
                return self.send_json(200, retriever.answer(question))
            if self.path == "/api/bookings":
                return self.send_json(201, {"booking": store.create(payload)})
            if self.path.startswith("/api/bookings/") and self.path.endswith("/cancel"):
                booking_id = self.path.split("/")[3]
                return self.send_json(200, {"booking": store.cancel(booking_id)})
            return self.send_json(404, {"error": "Endpoint not found."})
        except LookupError as exc:
            return self.send_json(404, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(500, {"error": "Unexpected server error."})

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Meridian Assistant running at http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

