"""Private file storage for employee photos and documents (v3).

Security posture (from the research blueprint, scaled to this app):
- allow-list extensions + magic-byte validation server-side
- size limits; sanitized random stored filenames; stored outside the static
  folder (instance/uploads) so files are never served directly
- served only through an authenticated route after an authorization check
- download logged; audit on upload/verify/reject
"""
import os
import secrets
from flask import current_app


PHOTO_DIR = "photos"
DOC_DIR = "docs"

PHOTO_MAX_BYTES = 2 * 1024 * 1024
DOC_MAX_BYTES = 10 * 1024 * 1024

DOC_CATEGORIES = [
    "Passport",
    "Emirates ID",
    "Residence Visa",
    "Labour Card",
    "Education Certificate",
    "Employment Contract",
    "Offer Letter",
    "Medical Certificate",
    "Driving Licence",
    "Other",
]

MAGIC = {
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "pdf": b"%PDF",
}


def upload_root():
    root = os.path.join(current_app.instance_path, "uploads")
    for sub in (PHOTO_DIR, DOC_DIR):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


def kind_from_bytes(data):
    """Return 'png' | 'jpg' | 'pdf' | None based on magic bytes."""
    for kind, sig in MAGIC.items():
        if data[:len(sig)] == sig:
            return kind
    return None


def valid_photo(data, filename):
    """JPG/PNG, max 2 MB. Returns (kind, error)."""
    if not data or len(data) > PHOTO_MAX_BYTES:
        return None, "Photo must be JPG or PNG and under 2 MB."
    kind = kind_from_bytes(data)
    if kind not in ("png", "jpg"):
        return None, "Photo must be a valid JPG or PNG file."
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg"):
        return None, "Photo extension must be .png, .jpg or .jpeg."
    return ("jpg" if kind == "jpg" else "png"), None


def valid_document(data, filename):
    """PDF/JPG/PNG, max 10 MB. Returns (kind, error)."""
    if not data or len(data) > DOC_MAX_BYTES:
        return None, "Document must be PDF, JPG or PNG and under 10 MB."
    kind = kind_from_bytes(data)
    if kind not in ("pdf", "png", "jpg"):
        return None, "Document must be a valid PDF, JPG or PNG file."
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "png", "jpg", "jpeg"):
        return None, "Document extension must be .pdf, .png, .jpg or .jpeg."
    return kind, None


def store_file(data, subdir, suffix):
    """Store bytes with a random filename; returns the stored name."""
    name = f"{secrets.token_hex(16)}.{suffix}"
    path = os.path.join(upload_root(), subdir, name)
    with open(path, "wb") as f:
        f.write(data)
    return name


def read_file(subdir, name):
    path = os.path.join(upload_root(), subdir, name)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def remove_file(subdir, name):
    try:
        os.remove(os.path.join(upload_root(), subdir, name))
    except OSError:
        pass


def manager_chain_ids(user):
    """User IDs in the reporting chain above `user` (direct manager up)."""
    ids, seen = [], set()
    cur = user.reporting_manager
    from app.models import User
    while cur and cur not in seen:
        seen.add(cur)
        ids.append(cur)
        mgr = User.query.get(cur)
        cur = mgr.reporting_manager if mgr else None
    return ids


def can_view_photo(viewer, target):
    """Photo visibility: self, manager chain, admin."""
    if viewer.id == target.id:
        return True
    if viewer.is_admin:
        return True
    return viewer.id in manager_chain_ids(target)


def salary_visible(viewer):
    """Salary permission: HR Manager (admin) or users granted can_view_salary."""
    return bool(viewer.is_admin or getattr(viewer, "can_view_salary", False))
