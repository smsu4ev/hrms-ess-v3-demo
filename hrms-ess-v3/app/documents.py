"""Employee photo + document vault routes (v3).

Permissions:
- Photo: upload self (Profile) or HR via Employee Central edit; view self /
  manager chain / admin through an authenticated route only.
- Documents: employee uploads own (status pending); HR uploads for anyone;
  HR verifies/rejects; downloads allowed for self and admin only
  (managers and other employees are denied).
"""
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, Response)
from flask_login import login_required, current_user

from app import db
from app.models import User, EmployeeDocument
from app.audit import record
from app.files import (DOC_CATEGORIES, valid_photo, valid_document, store_file,
                       read_file, PHOTO_DIR, DOC_DIR, can_view_photo)

docs_bp = Blueprint("docs", __name__)


def _hr_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


# ---------------- Photo ----------------

@docs_bp.route("/photo/<uid>")
@login_required
def photo(uid):
    """Authenticated photo serving (never a direct static URL)."""
    target = User.query.get_or_404(uid)
    if not can_view_photo(current_user, target):
        abort(403)
    if not target.photo_path:
        abort(404)
    record("photo_viewed", f"Photo viewed for {uid}",
           entity="user", entity_id=uid)
    db.session.commit()
    data = read_file(PHOTO_DIR, target.photo_path)
    if not data:
        abort(404)
    kind = "jpeg" if target.photo_path.endswith((".jpg", ".jpeg")) else "png"
    return Response(data, mimetype=f"image/{kind}",
                    headers={"Cache-Control": "private, max-age=3600"})


@docs_bp.route("/profile/photo", methods=["POST"])
@login_required
def profile_photo():
    """Employee uploads/updates own photo."""
    f = request.files.get("photo")
    data = f.read() if f else b""
    kind, err = valid_photo(data, f.filename if f else "")
    if err:
        flash(err, "error")
        return redirect(url_for("views.profile"))
    from app.files import remove_file
    if current_user.photo_path:
        remove_file(PHOTO_DIR, current_user.photo_path)
    current_user.photo_path = store_file(data, PHOTO_DIR, kind)
    record("photo_uploaded", f"photo updated for {current_user.id}",
           entity="employee", entity_id=current_user.id)
    db.session.commit()
    flash("Profile photo updated.", "success")
    return redirect(url_for("views.profile"))


# ---------------- Employee documents (ESS) ----------------

@docs_bp.route("/documents", methods=["GET", "POST"])
@login_required
def my_documents():
    if request.method == "POST":
        category = (request.form.get("category") or "").strip()
        title = (request.form.get("title") or "").strip()[:160]
        f = request.files.get("file")
        data = f.read() if f else b""
        if category not in DOC_CATEGORIES:
            flash("Invalid document category.", "error")
        elif not title:
            flash("A document title is required.", "error")
        else:
            kind, err = valid_document(data, f.filename if f else "")
            if err:
                flash(err, "error")
            else:
                issue = _parse_date(request.form.get("issue_date"))
                expiry = _parse_date(request.form.get("expiry_date"))
                # Supersede previous current document of the same category.
                old = EmployeeDocument.query.filter_by(
                    user_id=current_user.id, category=category,
                    superseded=False).all()
                for o in old:
                    o.superseded = True
                doc = EmployeeDocument(
                    user_id=current_user.id, category=category, title=title,
                    stored_name=store_file(data, DOC_DIR, kind),
                    original_name=(f.filename or "")[:200],
                    mime="application/pdf" if kind == "pdf"
                    else ("image/png" if kind == "png" else "image/jpeg"),
                    issue_date=issue, expiry_date=expiry,
                    status="pending", uploaded_by=current_user.id)
                db.session.add(doc)
                record("document_uploaded", f"{category}: {title}",
                       entity="document", entity_id=current_user.id,
                       field_name=category, new_value=title)
                db.session.commit()
                flash("Document uploaded. HR will verify it.", "success")
                return redirect(url_for("docs.my_documents"))
    items = EmployeeDocument.query.filter_by(
        user_id=current_user.id, superseded=False)
    history = EmployeeDocument.query.filter_by(
        user_id=current_user.id, superseded=True)
    # v3.6: document-type filter.
    doc_type = (request.args.get("doc_type") or "").strip()
    if doc_type in DOC_CATEGORIES:
        items = items.filter_by(category=doc_type)
        history = history.filter_by(category=doc_type)
    items = items.order_by(EmployeeDocument.created_at.desc()).all()
    history = history.order_by(EmployeeDocument.created_at.desc()).limit(20).all()
    return render_template("documents.html", items=items, history=history,
                           categories=DOC_CATEGORIES, doc_type=doc_type)


def _safe_download_name(name):
    """Sanitize a filename for Content-Disposition: no quotes, no CR/LF
    (header injection), safe fallback when empty."""
    cleaned = (name or "document").replace("\r", "").replace("\n", "")\
        .replace('"', "").strip()
    return cleaned or "document"


@docs_bp.route("/documents/<int:doc_id>/download")
@login_required
def download(doc_id):
    doc = EmployeeDocument.query.get_or_404(doc_id)
    if doc.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    data = read_file(DOC_DIR, doc.stored_name)
    if not data:
        abort(404)
    record("document_downloaded", f"{doc.category}: {doc.title}",
           entity="document", entity_id=doc.user_id,
           field_name=doc.category, new_value=doc.id)
    db.session.commit()
    return Response(
        data, mimetype=doc.mime or "application/octet-stream",
        headers={"Content-Disposition":
                 f"attachment; filename=\"{_safe_download_name(doc.original_name)}\"",
                 "X-Content-Type-Options": "nosniff"})


# ---------------- HR document management ----------------

@docs_bp.route("/hrms/documents", methods=["GET", "POST"])
@login_required
@_hr_required
def hr_documents():
    """HR view of the whole vault + upload for any employee + verify/reject."""
    q = EmployeeDocument.query.filter_by(superseded=False)
    user_id = (request.args.get("user") or "").strip()
    category = (request.args.get("category") or "").strip()
    status = (request.args.get("status") or "").strip()
    expiring = request.args.get("expiring") == "1"
    if user_id:
        q = q.filter(EmployeeDocument.user_id == user_id)
    if category in DOC_CATEGORIES:
        q = q.filter_by(category=category)
    if status in ("pending", "verified", "rejected"):
        q = q.filter_by(status=status)
    if expiring:
        from datetime import date, timedelta
        q = q.filter(EmployeeDocument.expiry_date.isnot(None),
                     EmployeeDocument.expiry_date <= date.today() + timedelta(days=60))
    items = q.order_by(EmployeeDocument.created_at.desc()).limit(300).all()
    employees = User.query.order_by(User.name).all()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "verify" or action == "reject":
            doc = EmployeeDocument.query.get_or_404(int(request.form.get("doc_id", 0)))
            note = (request.form.get("note") or "").strip()[:300]
            old = doc.status
            doc.status = "verified" if action == "verify" else "rejected"
            doc.verified_by = current_user.id
            doc.verify_note = note or None
            record(f"document_{doc.status}",
                   f"{doc.category}: {doc.title} - {note or 'no note'}",
                   entity="document", entity_id=doc.user_id,
                   field_name=doc.category, old_value=old,
                   new_value=doc.status)
            db.session.commit()
            flash(f"Document {doc.status}.", "success")
            return redirect(url_for("docs.hr_documents"))
        if action == "upload":
            target_id = (request.form.get("user_id") or "").strip()
            target = User.query.get(target_id)
            category = (request.form.get("category") or "").strip()
            title = (request.form.get("title") or "").strip()[:160]
            f = request.files.get("file")
            data = f.read() if f else b""
            if not target:
                flash("Select an employee.", "error")
            elif category not in DOC_CATEGORIES or not title:
                flash("Category and title are required.", "error")
            else:
                kind, err = valid_document(data, f.filename if f else "")
                if err:
                    flash(err, "error")
                else:
                    old = EmployeeDocument.query.filter_by(
                        user_id=target.id, category=category,
                        superseded=False).all()
                    for o in old:
                        o.superseded = True
                    doc = EmployeeDocument(
                        user_id=target.id, category=category, title=title,
                        stored_name=store_file(data, DOC_DIR, kind),
                        original_name=(f.filename or "")[:200],
                        mime="application/pdf" if kind == "pdf"
                        else ("image/png" if kind == "png" else "image/jpeg"),
                        issue_date=_parse_date(request.form.get("issue_date")),
                        expiry_date=_parse_date(request.form.get("expiry_date")),
                        status="verified", verified_by=current_user.id,
                        uploaded_by=current_user.id)
                    db.session.add(doc)
                    record("document_uploaded",
                           f"HR upload {category}: {title} for {target.id}",
                           entity="document", entity_id=target.id,
                           field_name=category, new_value=title)
                    db.session.commit()
                    flash("Document uploaded and verified.", "success")
                    return redirect(url_for("docs.hr_documents"))
    return render_template("hr_documents.html", items=items,
                           employees=employees, categories=DOC_CATEGORIES,
                           user_id=user_id, category=category, status=status,
                           expiring=expiring)


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
