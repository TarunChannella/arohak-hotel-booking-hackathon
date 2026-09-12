"""Organizations, multi-hotel support and receptionist assignments.

Hierarchy: Platform -> Organization -> Hotel -> Room -> Booking.

Scoping rules enforced here and in server.py:

- PRODUCT_ADMIN works at platform level and may manage organizations.
- ORGANIZATION_ADMIN (and the original ADMIN, which is the same role under its
  earlier name) manages only its own organization.
- RECEPTIONIST works only on the hotels it has been assigned, inside its own
  organization.
- CUSTOMER browses organizations and hotels and books rooms.

Every read and write below takes the acting user and filters by organization,
so a cross-organization request returns nothing rather than someone else's data.
"""
import uuid
from datetime import UTC, datetime

import db
from auth import PermissionError_, require_user

PRODUCT_ADMIN = "PRODUCT_ADMIN"
# ADMIN is the original single-hotel name for an organization administrator.
ORG_ADMIN_ROLES = ("ORGANIZATION_ADMIN", "ADMIN")
STAFF_ROLES = (PRODUCT_ADMIN,) + ORG_ADMIN_ROLES + ("RECEPTIONIST",)


def is_product_admin(user):
    return bool(user) and user["role"] == PRODUCT_ADMIN


def is_org_admin(user):
    return bool(user) and user["role"] in ORG_ADMIN_ROLES


def is_receptionist(user):
    return bool(user) and user["role"] == "RECEPTIONIST"


def require_same_organization(user, organization_id):
    """A product admin crosses organizations; nobody else does."""
    require_user(user)
    if is_product_admin(user):
        return True
    if user["organization_id"] != organization_id:
        raise PermissionError_("That organization is outside your access.")
    return True


class OrganizationStore:
    def __init__(self, path=None):
        self.path = db.database_path(path)
        db.initialize(self.path)

    # ---------- organizations ----------

    def create(self, user, payload):
        if not is_product_admin(user):
            raise PermissionError_("Only a product administrator may create organizations.")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("Organization name is required.")
        organization_id = "ORG-" + uuid.uuid4().hex[:8].upper()
        with db.connect(self.path) as conn:
            conn.execute("INSERT INTO organizations VALUES (?,?,?,?)",
                         (organization_id, name, "ACTIVE",
                          datetime.now(UTC).isoformat(timespec="seconds")))
        return self.get(user, organization_id)

    def get(self, user, organization_id):
        require_same_organization(user, organization_id)
        with db.connect(self.path) as conn:
            row = conn.execute("SELECT * FROM organizations WHERE id=?", (organization_id,)).fetchone()
        return dict(row) if row else None

    def list(self, user):
        """Product admins see every organization; everyone else sees their own."""
        require_user(user)
        with db.connect(self.path) as conn:
            if is_product_admin(user):
                rows = conn.execute("SELECT * FROM organizations ORDER BY name").fetchall()
            else:
                rows = conn.execute("SELECT * FROM organizations WHERE id=?",
                                    (user["organization_id"],)).fetchall()
        return [dict(r) for r in rows]

    def list_public(self):
        """Active organizations a customer may browse."""
        with db.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT id, name FROM organizations WHERE status='ACTIVE' ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def update(self, user, organization_id, payload):
        if not is_product_admin(user):
            raise PermissionError_("Only a product administrator may manage organizations.")
        updates = {k: payload[k] for k in ("name", "status") if k in payload}
        if not updates:
            raise ValueError("No organization fields supplied.")
        if "status" in updates and updates["status"] not in ("ACTIVE", "INACTIVE"):
            raise ValueError("Organization status must be ACTIVE or INACTIVE.")
        if "name" in updates and not str(updates["name"]).strip():
            raise ValueError("Organization name cannot be empty.")
        assignments = ", ".join(f"{k}=?" for k in updates)
        with db.connect(self.path) as conn:
            cursor = conn.execute(f"UPDATE organizations SET {assignments} WHERE id=?",
                                  (*updates.values(), organization_id))
            if cursor.rowcount == 0:
                raise LookupError("Organization not found.")
        return self.get(user, organization_id)

    # ---------- hotels within an organization ----------

    def create_hotel(self, user, payload):
        """Organization admins add hotels to their own organization."""
        require_user(user)
        organization_id = str(payload.get("organization_id") or user["organization_id"])
        if not (is_product_admin(user) or is_org_admin(user)):
            raise PermissionError_("Only an administrator may add a hotel.")
        require_same_organization(user, organization_id)

        name = str(payload.get("name", "")).strip()
        city = str(payload.get("city", "")).strip()
        address = str(payload.get("address", "")).strip()
        if not name or not city or not address:
            raise ValueError("Hotel name, address and city are required.")
        hotel_id = str(payload.get("id") or "HTL-" + uuid.uuid4().hex[:8].upper()).strip()
        with db.connect(self.path) as conn:
            if conn.execute("SELECT 1 FROM hotels WHERE id=?", (hotel_id,)).fetchone():
                raise ValueError("That hotel id already exists.")
            conn.execute("INSERT INTO hotels VALUES (?,?,?,?,?,?,?,?,?)",
                         (hotel_id, organization_id, name, address, city,
                          str(payload.get("description", "")).strip(),
                          str(payload.get("contact_number", "")).strip(),
                          str(payload.get("email", "")).strip(), "ACTIVE"))
        return self.get_hotel(user, hotel_id)

    def get_hotel(self, user, hotel_id):
        with db.connect(self.path) as conn:
            row = conn.execute("SELECT * FROM hotels WHERE id=?", (hotel_id,)).fetchone()
        if not row:
            return None
        hotel = dict(row)
        require_same_organization(user, hotel["organization_id"])
        if is_receptionist(user) and not self.receptionist_can_access(user["id"], hotel_id):
            raise PermissionError_("That hotel is not assigned to you.")
        return hotel

    def list_hotels(self, user=None, organization_id=None):
        """Hotels visible to this user.

        Staff see their own organization's hotels, a receptionist only the ones
        assigned to them, and an anonymous customer sees active hotels in the
        organization they are browsing.
        """
        # Customers browse the platform, so they are scoped by the organization
        # they picked, not by the one their account happens to belong to.
        browsing = not user or user["role"] == "CUSTOMER"
        query = "SELECT * FROM hotels WHERE 1=1"
        params = []
        if browsing:
            if organization_id:
                query += " AND organization_id=?"
                params.append(organization_id)
        elif not is_product_admin(user):
            query += " AND organization_id=?"
            params.append(user["organization_id"])
        elif organization_id:
            query += " AND organization_id=?"
            params.append(organization_id)
        if browsing:
            query += " AND status='ACTIVE'"
        query += " ORDER BY name"
        with db.connect(self.path) as conn:
            rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        if user and is_receptionist(user):
            allowed = set(self.assigned_hotel_ids(user["id"]))
            rows = [r for r in rows if r["id"] in allowed]
        return rows

    # ---------- receptionist assignments ----------

    def assign_receptionist(self, user, receptionist_id, hotel_id):
        if not (is_product_admin(user) or is_org_admin(user)):
            raise PermissionError_("Only an administrator may assign a receptionist.")
        with db.connect(self.path) as conn:
            hotel = conn.execute("SELECT * FROM hotels WHERE id=?", (hotel_id,)).fetchone()
            if not hotel:
                raise LookupError("Hotel not found.")
            staff = conn.execute("SELECT * FROM users WHERE id=?", (receptionist_id,)).fetchone()
            if not staff:
                raise LookupError("User not found.")
            if staff["role"] != "RECEPTIONIST":
                raise ValueError("Only a receptionist can be assigned to a hotel.")
            require_same_organization(user, hotel["organization_id"])
            if staff["organization_id"] != hotel["organization_id"]:
                raise PermissionError_("That receptionist belongs to another organization.")
            conn.execute(
                "INSERT OR IGNORE INTO receptionist_hotels VALUES (?,?,?)",
                (receptionist_id, hotel_id, datetime.now(UTC).isoformat(timespec="seconds")))
        return self.assigned_hotel_ids(receptionist_id)

    def unassign_receptionist(self, user, receptionist_id, hotel_id):
        if not (is_product_admin(user) or is_org_admin(user)):
            raise PermissionError_("Only an administrator may change assignments.")
        with db.connect(self.path) as conn:
            conn.execute("DELETE FROM receptionist_hotels WHERE user_id=? AND hotel_id=?",
                         (receptionist_id, hotel_id))
        return self.assigned_hotel_ids(receptionist_id)

    def assigned_hotel_ids(self, user_id):
        with db.connect(self.path) as conn:
            rows = conn.execute("SELECT hotel_id FROM receptionist_hotels WHERE user_id=?",
                                (user_id,)).fetchall()
        return [r["hotel_id"] for r in rows]

    def receptionist_can_access(self, user_id, hotel_id):
        return hotel_id in self.assigned_hotel_ids(user_id)

    # ---------- authorisation used by the routes ----------

    def accessible_hotel_ids(self, user):
        """Hotel ids this user may act on. None means no restriction."""
        require_user(user)
        if is_product_admin(user):
            return None
        if is_receptionist(user):
            return self.assigned_hotel_ids(user["id"])
        return [h["id"] for h in self.list_hotels(user)]

    def require_hotel_access(self, user, hotel_id):
        """Raise unless the user may act on this hotel."""
        allowed = self.accessible_hotel_ids(user)
        if allowed is None:
            return True
        if hotel_id not in allowed:
            raise PermissionError_("That hotel is outside your access.")
        return True
