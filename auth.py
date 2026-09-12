"""Registration, login, sessions and role authorization.

Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user random salt.
Session tokens are random 256-bit values kept server-side; the client only ever
holds an opaque token. No secret is hardcoded anywhere in this file.
"""
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import db

ROLES = ("ADMIN", "RECEPTIONIST", "CUSTOMER")
SELF_SERVICE_ROLES = ("CUSTOMER",)  # roles a stranger may pick at registration
STAFF_ROLES = ("ADMIN", "RECEPTIONIST")
SESSION_HOURS = 12
PBKDF2_ROUNDS = 200_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Raised for bad credentials or invalid registration input."""


class PermissionError_(Exception):
    """Raised when an authenticated user lacks the required role."""


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def _now():
    return datetime.now(UTC)


def _public(row):
    """Strip the password hash before a user record leaves this module."""
    return {"id": row["id"], "name": row["name"], "email": row["email"],
            "role": row["role"], "organization_id": row["organization_id"]}


class AuthStore:
    def __init__(self, path=None):
        self.path = db.database_path(path)
        db.initialize(self.path)

    # ---------- registration and login ----------

    def register(self, payload, allow_staff=False):
        name = str(payload.get("name", "")).strip()
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", ""))
        role = str(payload.get("role", "CUSTOMER")).strip().upper() or "CUSTOMER"

        if not name:
            raise AuthError("Name is required.")
        if not EMAIL_RE.match(email):
            raise AuthError("A valid email address is required.")
        if len(password) < 8:
            raise AuthError("Password must be at least 8 characters.")
        if role not in ROLES:
            raise AuthError("Role must be ADMIN, RECEPTIONIST or CUSTOMER.")
        if role not in SELF_SERVICE_ROLES and not allow_staff:
            raise AuthError("Staff accounts must be created by an administrator.")

        user_id = "USR-" + uuid.uuid4().hex[:8].upper()
        with db.connect(self.path) as conn:
            existing = conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                raise AuthError("An account with this email already exists.")
            conn.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                (user_id, db.DEFAULT_ORG_ID, name, email, hash_password(password),
                 role, _now().isoformat(timespec="seconds")),
            )
        return self.get_user(user_id)

    def login(self, email, password):
        email = str(email or "").strip().lower()
        with db.connect(self.path) as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        # Always run a hash comparison so a missing account and a wrong password
        # take a similar amount of time.
        stored = row["password_hash"] if row else hash_password(secrets.token_hex(8))
        if not verify_password(str(password or ""), stored) or not row:
            raise AuthError("Invalid email or password.")
        return self.create_session(row["id"]), _public(row)

    # ---------- sessions ----------

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        now = _now()
        with db.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO sessions VALUES (?,?,?,?)",
                (token, user_id, now.isoformat(timespec="seconds"),
                 (now + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")),
            )
        return token

    def user_for_token(self, token):
        if not token:
            return None
        with db.connect(self.path) as conn:
            row = conn.execute(
                "SELECT u.* , s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
                (token,),
            ).fetchone()
            if not row:
                return None
            if datetime.fromisoformat(row["expires_at"]) < _now():
                conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                return None
        return _public(row)

    def logout(self, token):
        with db.connect(self.path) as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))

    def get_user(self, user_id):
        with db.connect(self.path) as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _public(row) if row else None

    def list_users(self):
        with db.connect(self.path) as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY role, name").fetchall()
        return [_public(r) for r in rows]


# ---------- authorization helpers ----------

def require_user(user):
    if not user:
        raise PermissionError_("Authentication required.")
    return user


def require_role(user, *allowed):
    require_user(user)
    if user["role"] not in allowed:
        raise PermissionError_(f"This action requires one of: {', '.join(allowed)}.")
    return user


def is_staff(user):
    return bool(user) and user["role"] in STAFF_ROLES
