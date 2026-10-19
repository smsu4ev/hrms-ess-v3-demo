"""Retail-fixture specialised registers (v3.1).

Certifications, projects & site deployments, accommodation/transport,
PPE & uniform, HSE incidents, contractor workers, separations.
Permissions: admin/HR manage (write), managers read-only, HSE incident
creation open to managers; employees are denied at register level (403)
and see only their own records on their profile page.
"""
from datetime import date
from functools import wraps

from flask import Blueprint, request, render_template, abort, redirect, url_for, flash
from flask_login import login_required, current_user

from app import db
from app.models import (User, Certification, Project, Deployment,
                        FacilityAssignment, PPEIssue, HSEIncident,
                        ContractorWorker, SeparationRecord, AssetCustody)
from app.audit import record
from app.hrms import admin_required

retail_bp = Blueprint("retail", __name__, url_prefix="/hrms")


def _mgr_required(fn):
    """Any manager (admin included) may view; employees get 403."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_manager:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _write_required(fn):
    """Admin-only write operations."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _user_options(selected=""):
    return [("", "- select employee -")] + [
        (u.id, f"{u.name} ({u.id})")
        for u in User.query.filter(User.is_active.is_(True))
                          .order_by(User.name).all()]


def _parse_date(s):
    s = (s or "").strip()
    return date.fromisoformat(s) if s else None


def _register_ctx(title, description, columns, rows, add_fields, can_create,
                  summary=None):
    return dict(title=title, description=description, columns=columns,
                rows=rows, add_fields=add_fields, can_write=current_user.is_admin,
                can_create=can_create, summary=summary)


# ---------------- Certifications ----------------

@retail_bp.route("/certifications", methods=["GET", "POST"])
@login_required
@_mgr_required
def certifications():
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        db.session.add(Certification(
            user_id=f.get("user_id"), name=f.get("name"), issuer=f.get("issuer"),
            issue_date=_parse_date(f.get("issue_date")),
            expiry_date=_parse_date(f.get("expiry_date")),
            notes=f.get("notes"), created_by=current_user.id))
        record("certification_added", f.get("name"), entity="certification",
               entity_id=f.get("user_id"))
        db.session.commit()
        flash("Certification recorded.", "success")
        return redirect(url_for("retail.certifications"))
    users = {u.id: u for u in User.query.all()}
    today = date.today()
    rows = []
    for c in Certification.query.order_by(Certification.expiry_date).all():
        u = users.get(c.user_id)
        days = (c.expiry_date - today).days if c.expiry_date else None
        rows.append({
            "Employee": f"{u.name} ({u.id})" if u else c.user_id,
            "Certification": c.name, "Issuer": c.issuer or "",
            "Issue Date": c.issue_date.isoformat() if c.issue_date else "",
            "Expiry Date": c.expiry_date.isoformat() if c.expiry_date else "",
            "Days Left": days if days is not None else "",
            "delete_url": url_for("retail.certification_delete", cid=c.id)})
    ctx = _register_ctx(
        "Certifications", "Skill and compliance certifications per employee. "
        "Certifications expiring within 60 days also appear on the Compliance page.",
        ["Employee", "Certification", "Issuer", "Issue Date", "Expiry Date",
         "Days Left"], rows,
        [{"name": "user_id", "label": "Employee", "type": "select",
          "options": _user_options(), "required": True},
         {"name": "name", "label": "Certification name", "type": "text",
          "required": True, "placeholder": "e.g. Welding Trade, Fire Safety"},
         {"name": "issuer", "label": "Issuer", "type": "text"},
         {"name": "issue_date", "label": "Issue date", "type": "date"},
         {"name": "expiry_date", "label": "Expiry date", "type": "date"},
         {"name": "notes", "label": "Notes", "type": "text"}],
        can_create=current_user.is_admin)
    return render_template("retail_register.html", **ctx)


@retail_bp.route("/certifications/<int:cid>/delete", methods=["POST"])
@login_required
@_write_required
def certification_delete(cid):
    c = Certification.query.get_or_404(cid)
    record("certification_deleted", c.name, entity="certification",
           entity_id=c.user_id)
    db.session.delete(c)
    db.session.commit()
    flash("Certification deleted.", "success")
    return redirect(url_for("retail.certifications"))


# ---------------- Projects & Deployments ----------------

@retail_bp.route("/projects", methods=["GET", "POST"])
@login_required
@_mgr_required
def projects():
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        db.session.add(Project(name=f.get("name"), client=f.get("client"),
                               location=f.get("location"),
                               status=f.get("status") or "active",
                               created_by=current_user.id))
        record("project_created", f.get("name"), entity="project")
        db.session.commit()
        flash("Project created.", "success")
        return redirect(url_for("retail.projects"))
    rows = []
    for p in Project.query.order_by(Project.status, Project.name).all():
        deps = Deployment.query.filter_by(project_id=p.id).all()
        rows.append({"Project": p.name, "Client": p.client or "",
                     "Location": p.location or "", "Status": p.status.title(),
                     "Deployed Staff": len(deps),
                     "detail_url": url_for("retail.project_detail", pid=p.id)})
    ctx = _register_ctx(
        "Projects & Site Deployments",
        "Retail-fixture projects (store fit-outs, warehouse racking) and the "
        "staff deployed to each site with their site allowance.",
        ["Project", "Client", "Location", "Status", "Deployed Staff"], rows,
        [{"name": "name", "label": "Project name", "type": "text",
          "required": True, "placeholder": "e.g. Store Fit-Out — Dubai Mall Client"},
         {"name": "client", "label": "Client", "type": "text"},
         {"name": "location", "label": "Location", "type": "text"},
         {"name": "status", "label": "Status", "type": "select",
          "options": [("active", "Active"), ("completed", "Completed")]}],
        can_create=current_user.is_admin)
    return render_template("retail_register.html", **ctx)


@retail_bp.route("/projects/<int:pid>", methods=["GET", "POST"])
@login_required
@_mgr_required
def project_detail(pid):
    p = Project.query.get_or_404(pid)
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        try:
            allowance = float(f.get("site_allowance") or 0)
        except ValueError:
            allowance = 0.0
        db.session.add(Deployment(
            user_id=f.get("user_id"), project_id=p.id,
            from_date=_parse_date(f.get("from_date")),
            to_date=_parse_date(f.get("to_date")),
            site_allowance=allowance, created_by=current_user.id))
        record("deployment_added", f"{f.get('user_id')} -> {p.name}",
               entity="project", entity_id=str(p.id))
        db.session.commit()
        flash("Deployment recorded.", "success")
        return redirect(url_for("retail.project_detail", pid=pid))
    users = {u.id: u for u in User.query.all()}
    deps = Deployment.query.filter_by(project_id=p.id).order_by(
        Deployment.from_date.desc()).all()
    rows, total = [], 0.0
    for d in deps:
        u = users.get(d.user_id)
        rows.append({"Employee": f"{u.name} ({u.id})" if u else d.user_id,
                     "From": d.from_date.isoformat() if d.from_date else "",
                     "To": d.to_date.isoformat() if d.to_date else "",
                     "Site Allowance (AED)": f"{d.site_allowance:.2f}",
                     "delete_url": url_for("retail.deployment_delete", did=d.id)})
        total += d.site_allowance or 0
    return render_template(
        "retail_project_detail.html", project=p, rows=rows,
        total=total, user_options=_user_options(),
        can_write=current_user.is_admin)


@retail_bp.route("/deployments/<int:did>/delete", methods=["POST"])
@login_required
@_write_required
def deployment_delete(did):
    d = Deployment.query.get_or_404(did)
    pid = d.project_id
    record("deployment_deleted", d.user_id, entity="project",
           entity_id=str(pid))
    db.session.delete(d)
    db.session.commit()
    flash("Deployment removed.", "success")
    return redirect(url_for("retail.project_detail", pid=pid))


# ---------------- Accommodation & Transport ----------------

@retail_bp.route("/facilities", methods=["GET", "POST"])
@login_required
@_mgr_required
def facilities():
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        try:
            cost = float(f.get("monthly_cost") or 0)
        except ValueError:
            cost = 0.0
        db.session.add(FacilityAssignment(
            user_id=f.get("user_id"), kind=f.get("kind") or "accommodation",
            location_or_route=f.get("location_or_route"), monthly_cost=cost,
            from_date=_parse_date(f.get("from_date")),
            to_date=_parse_date(f.get("to_date")),
            created_by=current_user.id))
        record("facility_assigned", f"{f.get('kind')}: {f.get('location_or_route')}",
               entity="facility", entity_id=f.get("user_id"))
        db.session.commit()
        flash("Assignment recorded.", "success")
        return redirect(url_for("retail.facilities"))
    users = {u.id: u for u in User.query.all()}
    rows = []
    for a in FacilityAssignment.query.order_by(
            FacilityAssignment.user_id).all():
        u = users.get(a.user_id)
        rows.append({"Employee": f"{u.name} ({u.id})" if u else a.user_id,
                     "Kind": a.kind.title(),
                     "Location / Route": a.location_or_route or "",
                     "Monthly Cost (AED)": f"{a.monthly_cost:.2f}",
                     "From": a.from_date.isoformat() if a.from_date else "",
                     "To": a.to_date.isoformat() if a.to_date else "",
                     "delete_url": url_for("retail.facility_delete", fid=a.id)})
    ctx = _register_ctx(
        "Accommodation & Transport",
        "Factory staff housing and transport assignments with monthly cost.",
        ["Employee", "Kind", "Location / Route", "Monthly Cost (AED)",
         "From", "To"], rows,
        [{"name": "user_id", "label": "Employee", "type": "select",
          "options": _user_options(), "required": True},
         {"name": "kind", "label": "Kind", "type": "select",
          "options": [("accommodation", "Accommodation"),
                      ("transport", "Transport")]},
         {"name": "location_or_route", "label": "Location / Route",
          "type": "text"},
         {"name": "monthly_cost", "label": "Monthly cost (AED)",
          "type": "number"},
         {"name": "from_date", "label": "From", "type": "date"},
         {"name": "to_date", "label": "To", "type": "date"}],
        can_create=current_user.is_admin)
    return render_template("retail_register.html", **ctx)


@retail_bp.route("/facilities/<int:fid>/delete", methods=["POST"])
@login_required
@_write_required
def facility_delete(fid):
    a = FacilityAssignment.query.get_or_404(fid)
    record("facility_deleted", a.location_or_route, entity="facility",
           entity_id=a.user_id)
    db.session.delete(a)
    db.session.commit()
    flash("Assignment deleted.", "success")
    return redirect(url_for("retail.facilities"))


# ---------------- PPE & Uniform ----------------

@retail_bp.route("/ppe", methods=["GET", "POST"])
@login_required
@_mgr_required
def ppe():
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        try:
            qty = int(f.get("qty") or 1)
        except ValueError:
            qty = 1
        db.session.add(PPEIssue(
            user_id=f.get("user_id"), item=f.get("item"), size=f.get("size"),
            qty=qty, issued_date=_parse_date(f.get("issued_date")),
            created_by=current_user.id))
        record("ppe_issued", f.get("item"), entity="ppe",
               entity_id=f.get("user_id"))
        db.session.commit()
        flash("PPE issue recorded.", "success")
        return redirect(url_for("retail.ppe"))
    users = {u.id: u for u in User.query.all()}
    rows = []
    for i in PPEIssue.query.order_by(PPEIssue.issued_date.desc()).all():
        u = users.get(i.user_id)
        rows.append({"Employee": f"{u.name} ({u.id})" if u else i.user_id,
                     "Item": i.item, "Size": i.size or "", "Qty": i.qty,
                     "Issued": i.issued_date.isoformat() if i.issued_date else "",
                     "Returned": i.returned_date.isoformat() if i.returned_date else "—",
                     "action": ({"url": url_for("retail.ppe_return", iid=i.id),
                                 "label": "Mark Returned"}
                                if not i.returned_date else None)})
    ctx = _register_ctx(
        "PPE & Uniform",
        "PPE and uniform issuance log for factory and site staff.",
        ["Employee", "Item", "Size", "Qty", "Issued", "Returned"], rows,
        [{"name": "user_id", "label": "Employee", "type": "select",
          "options": _user_options(), "required": True},
         {"name": "item", "label": "Item", "type": "text", "required": True,
          "placeholder": "e.g. Safety helmet, Hi-vis vest"},
         {"name": "size", "label": "Size", "type": "text"},
         {"name": "qty", "label": "Quantity", "type": "number"},
         {"name": "issued_date", "label": "Issued date", "type": "date"}],
        can_create=current_user.is_admin)
    return render_template("retail_register.html", **ctx)


@retail_bp.route("/ppe/<int:iid>/return", methods=["POST"])
@login_required
@_write_required
def ppe_return(iid):
    i = PPEIssue.query.get_or_404(iid)
    i.returned_date = date.today()
    record("ppe_returned", i.item, entity="ppe", entity_id=i.user_id)
    db.session.commit()
    flash("PPE marked as returned.", "success")
    return redirect(url_for("retail.ppe"))


# ---------------- HSE Incidents ----------------

HSE_TYPES = ["Injury", "Near Miss", "Property Damage", "Unsafe Act"]
HSE_SEVERITIES = ["Low", "Medium", "High", "Critical"]


@retail_bp.route("/hse", methods=["GET", "POST"])
@login_required
@_mgr_required
def hse():
    # managers may create; admin/HR edit & close
    if request.method == "POST":
        f = request.form
        db.session.add(HSEIncident(
            date=_parse_date(f.get("date")) or date.today(),
            location=f.get("location"),
            employee_id=f.get("employee_id") or None,
            type=f.get("type") or "Near Miss",
            severity=f.get("severity") or "Low",
            description=f.get("description"), action_taken="",
            status="Open", created_by=current_user.id))
        record("hse_incident_created", f"{f.get('type')} @ {f.get('location')}",
               entity="hse")
        db.session.commit()
        flash("HSE incident logged.", "success")
        return redirect(url_for("retail.hse"))
    users = {u.id: u for u in User.query.all()}
    rows = []
    for i in HSEIncident.query.order_by(HSEIncident.date.desc()).all():
        u = users.get(i.employee_id) if i.employee_id else None
        rows.append({"Date": i.date.isoformat(), "Location": i.location or "",
                     "Employee": f"{u.name} ({u.id})" if u else "—",
                     "Type": i.type, "Severity": i.severity,
                     "Description": (i.description or "")[:90],
                     "Status": i.status,
                     "action": ({"url": url_for("retail.hse_close", iid=i.id),
                                 "label": "Close"}
                                if i.status == "Open" and current_user.is_admin
                                else None)})
    ctx = _register_ctx(
        "HSE Incidents",
        "Health, safety and environment incident log. Managers may log "
        "incidents; only HR/Admin can edit details and close them.",
        ["Date", "Location", "Employee", "Type", "Severity",
         "Description", "Status"], rows,
        [{"name": "date", "label": "Date", "type": "date", "required": True},
         {"name": "location", "label": "Location", "type": "text",
          "required": True},
         {"name": "employee_id", "label": "Employee (if any)",
          "type": "select", "options": _user_options()},
         {"name": "type", "label": "Type", "type": "select",
          "options": [(t, t) for t in HSE_TYPES]},
         {"name": "severity", "label": "Severity", "type": "select",
          "options": [(s, s) for s in HSE_SEVERITIES]},
         {"name": "description", "label": "Description", "type": "text"}],
        can_create=current_user.is_manager)
    return render_template("retail_register.html", **ctx)


@retail_bp.route("/hse/<int:iid>/close", methods=["POST"])
@login_required
@_write_required
def hse_close(iid):
    i = HSEIncident.query.get_or_404(iid)
    i.status = "Closed"
    action = (request.form.get("action_taken") or "").strip()
    if action:
        i.action_taken = action
    record("hse_incident_closed", f"{i.type} @ {i.location}", entity="hse",
           entity_id=str(iid))
    db.session.commit()
    flash("Incident closed.", "success")
    return redirect(url_for("retail.hse"))


# ---------------- Contractors ----------------

@retail_bp.route("/contractors", methods=["GET", "POST"])
@login_required
@_mgr_required
def contractors():
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        try:
            rate = float(f.get("monthly_rate") or 0)
        except ValueError:
            rate = 0.0
        db.session.add(ContractorWorker(
            name=f.get("name"), company=f.get("company"), trade=f.get("trade"),
            site=f.get("site"), start_date=_parse_date(f.get("start_date")),
            end_date=_parse_date(f.get("end_date")), monthly_rate=rate,
            status=f.get("status") or "active",
            created_by=current_user.id))
        record("contractor_added", f"{f.get('name')} ({f.get('company')})",
               entity="contractor")
        db.session.commit()
        flash("Contractor worker recorded.", "success")
        return redirect(url_for("retail.contractors"))
    rows, billing = [], {}
    for w in ContractorWorker.query.order_by(ContractorWorker.company,
                                             ContractorWorker.name).all():
        rows.append({"Name": w.name, "Company": w.company, "Trade": w.trade or "",
                     "Site": w.site or "",
                     "Start": w.start_date.isoformat() if w.start_date else "",
                     "End": w.end_date.isoformat() if w.end_date else "",
                     "Monthly Rate (AED)": f"{w.monthly_rate:.2f}",
                     "Status": w.status.title(),
                     "delete_url": url_for("retail.contractor_delete", wid=w.id)})
        if w.status == "active":
            billing[w.company] = billing.get(w.company, 0.0) + (w.monthly_rate or 0)
    summary = "<strong>Monthly billing (active workers):</strong> " + "; ".join(
        f"{c}: AED {v:,.2f}" for c, v in sorted(billing.items())) \
        if billing else "No active contractor workers."
    ctx = _register_ctx(
        "Contractor Workers",
        "Contractor and seasonal worker register with billing rates.",
        ["Name", "Company", "Trade", "Site", "Start", "End",
         "Monthly Rate (AED)", "Status"], rows,
        [{"name": "name", "label": "Name", "type": "text", "required": True},
         {"name": "company", "label": "Company", "type": "text",
          "required": True},
         {"name": "trade", "label": "Trade", "type": "text"},
         {"name": "site", "label": "Site", "type": "text"},
         {"name": "start_date", "label": "Start date", "type": "date"},
         {"name": "end_date", "label": "End date", "type": "date"},
         {"name": "monthly_rate", "label": "Monthly rate (AED)",
          "type": "number"},
         {"name": "status", "label": "Status", "type": "select",
          "options": [("active", "Active"), ("ended", "Ended")]}],
        can_create=current_user.is_admin, summary=summary)
    return render_template("retail_register.html", **ctx)


@retail_bp.route("/contractors/<int:wid>/delete", methods=["POST"])
@login_required
@_write_required
def contractor_delete(wid):
    w = ContractorWorker.query.get_or_404(wid)
    record("contractor_deleted", f"{w.name} ({w.company})", entity="contractor")
    db.session.delete(w)
    db.session.commit()
    flash("Contractor worker removed.", "success")
    return redirect(url_for("retail.contractors"))


# ---------------- Asset & Tool Custody ----------------

ASSET_CATEGORIES = ["Tool", "IT Asset", "Vehicle", "Other"]
ASSET_STATUSES = ["Issued", "Returned", "Lost"]


def _unreturned_assets(user_id):
    return AssetCustody.query.filter_by(user_id=user_id,
                                        status="Issued",
                                        returned_date=None).count()


@retail_bp.route("/assets", methods=["GET", "POST"])
@login_required
@_mgr_required
def assets():
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        try:
            value = float(f.get("replacement_value") or 0)
        except ValueError:
            value = 0.0
        db.session.add(AssetCustody(
            user_id=f.get("user_id"), item_name=f.get("item_name"),
            category=f.get("category") or "Tool",
            serial_no=f.get("serial_no"), replacement_value=value,
            issued_date=_parse_date(f.get("issued_date")),
            status="Issued", note=f.get("note"),
            created_by=current_user.id))
        record("asset_issued", f.get("item_name"), entity="asset",
               entity_id=f.get("user_id"))
        db.session.commit()
        flash("Asset issue recorded.", "success")
        return redirect(url_for("retail.assets"))
    users = {u.id: u for u in User.query.all()}
    rows = []
    for a in AssetCustody.query.order_by(AssetCustody.issued_date.desc()).all():
        u = users.get(a.user_id)
        rows.append({"Employee": f"{u.name} ({u.id})" if u else a.user_id,
                     "Item": a.item_name, "Category": a.category,
                     "Serial No": a.serial_no or "",
                     "Value (AED)": f"{a.replacement_value:.2f}",
                     "Issued": a.issued_date.isoformat() if a.issued_date else "",
                     "Returned": a.returned_date.isoformat()
                     if a.returned_date else "—",
                     "Status": a.status,
                     "delete_url": url_for("retail.asset_delete", aid=a.id),
                     "action": ({"url": url_for("retail.asset_return", aid=a.id),
                                 "label": "Mark Returned"}
                                if a.is_outstanding else None)})
    ctx = _register_ctx(
        "Asset & Tool Custody",
        "Returnable assets (tools, IT assets, vehicles) in employee custody. "
        "Unreturned items are flagged in the Separation register.",
        ["Employee", "Item", "Category", "Serial No", "Value (AED)",
         "Issued", "Returned", "Status"], rows,
        [{"name": "user_id", "label": "Employee", "type": "select",
          "options": _user_options(), "required": True},
         {"name": "item_name", "label": "Item name", "type": "text",
          "required": True, "placeholder": "e.g. DeWalt drill, Laptop"},
         {"name": "category", "label": "Category", "type": "select",
          "options": [(c, c) for c in ASSET_CATEGORIES]},
         {"name": "serial_no", "label": "Serial no", "type": "text"},
         {"name": "replacement_value", "label": "Replacement value (AED)",
          "type": "number"},
         {"name": "issued_date", "label": "Issued date", "type": "date"},
         {"name": "note", "label": "Note", "type": "text"}],
        can_create=current_user.is_admin)
    return render_template("retail_register.html", **ctx)


@retail_bp.route("/assets/<int:aid>/return", methods=["POST"])
@login_required
@_write_required
def asset_return(aid):
    a = AssetCustody.query.get_or_404(aid)
    a.returned_date = date.today()
    a.status = "Returned"
    record("asset_returned", a.item_name, entity="asset",
           entity_id=a.user_id)
    db.session.commit()
    flash("Asset marked as returned.", "success")
    return redirect(url_for("retail.assets"))


@retail_bp.route("/assets/<int:aid>/delete", methods=["POST"])
@login_required
@_write_required
def asset_delete(aid):
    a = AssetCustody.query.get_or_404(aid)
    record("asset_deleted", a.item_name, entity="asset",
           entity_id=a.user_id)
    db.session.delete(a)
    db.session.commit()
    flash("Asset record deleted.", "success")
    return redirect(url_for("retail.assets"))


# ---------------- Training Matrix (read-only) ----------------

@retail_bp.route("/training-matrix")
@login_required
@_mgr_required
def training_matrix():
    dept = request.args.get("department", "")
    q = User.query.filter(User.is_active.is_(True))
    if dept:
        q = q.filter(User.department == dept)
    users = q.order_by(User.name).all()
    certs = Certification.query.all()
    names = sorted({c.name for c in certs if c.name})
    by_user = {}
    for c in certs:
        by_user.setdefault(c.user_id, []).append(c)
    today = date.today()
    rows = []
    for u in users:
        cells = []
        for nm in names:
            matches = [c for c in by_user.get(u.id, []) if c.name == nm]
            if not matches:
                cells.append(("", "none"))
                continue
            c = max(matches,
                    key=lambda x: x.expiry_date or date.min)
            if c.expiry_date and c.expiry_date < today:
                cells.append((c.expiry_date.isoformat(), "expired"))
            elif c.expiry_date and (c.expiry_date - today).days <= 60:
                cells.append((c.expiry_date.isoformat(), "expiring"))
            elif c.expiry_date:
                cells.append((c.expiry_date.isoformat(), "valid"))
            else:
                cells.append(("Recorded", "valid"))
        rows.append((u, cells))
    return render_template("hr_training_matrix.html", names=names,
                           rows=rows, dept=dept)

# ---------------- Separations (register CRUD) ----------------

SEPARATION_TYPES = ["Resignation", "Termination", "Contract End",
                    "Retirement", "Absconding", "Other"]
SETTLEMENT_STATUSES = ["Pending", "In Process", "Settled"]


@retail_bp.route("/separations", methods=["GET", "POST"])
@login_required
@_mgr_required
def separations():
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        try:
            notice = int(f.get("notice_days")) if f.get("notice_days") else None
        except ValueError:
            notice = None
        try:
            amount = float(f.get("settlement_amount") or 0)
        except ValueError:
            amount = 0.0
        db.session.add(SeparationRecord(
            employee_code=f.get("employee_code"), employee_name=f.get("employee_name"),
            department=f.get("department"), designation=f.get("designation"),
            joining_date=_parse_date(f.get("joining_date")),
            separation_date=_parse_date(f.get("separation_date")),
            separation_type=f.get("separation_type") or "Resignation",
            reason=f.get("reason"), notice_days=notice,
            final_settlement_status=f.get("final_settlement_status") or "Pending",
            settlement_amount=amount, created_by=current_user.id))
        record("separation_recorded",
               f"{f.get('employee_code')} {f.get('separation_type')}",
               entity="separation", entity_id=f.get("employee_code"))
        db.session.commit()
        flash("Separation record saved.", "success")
        return redirect(url_for("retail.separations"))
    users = {u.id: u for u in User.query.all()}
    rows = []
    for r in SeparationRecord.query.order_by(
            SeparationRecord.separation_date.desc()).all():
        pending = _unreturned_assets(r.employee_code)
        rows.append({"ID": r.id, "Employee ID": r.employee_code,
                     "Name": r.employee_name,
                     "Department": r.department or "",
                     "Separation Date": r.separation_date.isoformat()
                     if r.separation_date else "",
                     "Type": r.separation_type,
                     "Tenure (yrs)": r.tenure_years if r.tenure_years is not None else "",
                     "Settlement": r.final_settlement_status,
                     "Assets Pending": (f"{pending} unreturned"
                                        if pending else ""),
                     "delete_url": url_for("retail.separation_delete", rid=r.id),
                     # v3.2: jump to the Full & Final settlement for matched users.
                     "settlement_url": (url_for("settlement.new",
                                                user_id=r.employee_code)
                                        if r.employee_code in users else None)})
    # separated users not yet in the register
    rec_codes = {r.employee_code for r in SeparationRecord.query.all()}
    user_rows = []
    for u in User.query.filter(User.separation_date.isnot(None)).all():
        if u.id in rec_codes:
            continue
        pending = _unreturned_assets(u.id)
        user_rows.append({"Employee ID": u.id, "Name": u.name,
                          "Department": u.department or "",
                          "Separation Date": u.separation_date.isoformat()
                          if u.separation_date else "",
                          "Type": u.separation_type or "",
                          "Assets Pending": (f"{pending} unreturned"
                                             if pending else "")})
    return render_template(
        "retail_separations.html", rows=rows, user_rows=user_rows,
        can_write=current_user.is_admin, types=SEPARATION_TYPES,
        statuses=SETTLEMENT_STATUSES, users=users)


@retail_bp.route("/separations/<int:rid>/delete", methods=["POST"])
@login_required
@_write_required
def separation_delete(rid):
    r = SeparationRecord.query.get_or_404(rid)
    record("separation_deleted", r.employee_code, entity="separation",
           entity_id=r.employee_code)
    db.session.delete(r)
    db.session.commit()
    flash("Separation record deleted.", "success")
    return redirect(url_for("retail.separations"))
