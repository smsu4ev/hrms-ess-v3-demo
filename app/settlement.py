"""Full & Final settlement (v3.2; disbursement split in v3.4.1):
draft -> calculated -> approved -> disbursed -> paid.

Permissions: admin OR a user granted salary visibility by the HR Manager
(hr_required); managers without that permission get 403 (salary data).
Approval enforces creator != approver (SoD, same rule as payroll runs).
Settlement detail views are audited.
"""
import json
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, request, render_template, abort, redirect, url_for, flash
from flask_login import login_required, current_user

from app import db
from app.models import (User, FinalSettlement, SettlementLine, SeparationRecord,
                        Loan, LeaveBalance, PPEIssue, LeaveRequest, AssetCustody,
                        AttendanceRecord)
from app.audit import record
from app.hrms import hr_required
from app import uae
from app import payroll as eng
import config as app_config

settlement_bp = Blueprint("settlement", __name__, url_prefix="/hrms")

SEPARATION_TYPES = ["Resignation", "Termination", "Contract End",
                    "Retirement", "Absconding", "Other"]


def unpaid_absence_days(user_id, start, end):
    """Days excluded from gratuity service (Art. 51): approved unpaid leave
    days + unexcused absence days (AttendanceRecord status 'absent') between
    joining and the last working day.

    v3.4.1 refinement: an 'absent' day inside an APPROVED leave period of
    any other type (e.g. sick leave marked absent by a bulk import) is
    approved statutory leave, not unexcused absence — it must NOT be
    counted. Unpaid-leave days count once (absent days inside an unpaid
    period are not double counted).
    """
    if not start or not end or end < start:
        return 0
    covered = set()  # dates covered by ANY approved leave period
    for r in (LeaveRequest.query
              .filter(LeaveRequest.user_id == user_id,
                      LeaveRequest.status == "approved",
                      LeaveRequest.start_date <= end,
                      LeaveRequest.end_date >= start).all()):
        lo = max(r.start_date, start)
        hi = min(r.end_date, end)
        d = lo
        while d <= hi:
            covered.add(d)
            d = d + timedelta(days=1)
    n = 0
    for r in (LeaveRequest.query
              .filter(LeaveRequest.user_id == user_id,
                      LeaveRequest.leave_type == "unpaid",
                      LeaveRequest.status == "approved",
                      LeaveRequest.start_date <= end,
                      LeaveRequest.end_date >= start).all()):
        lo = max(r.start_date, start)
        hi = min(r.end_date, end)
        if hi >= lo:
            n += (hi - lo).days + 1
    n += (AttendanceRecord.query
          .filter(AttendanceRecord.user_id == user_id,
                  AttendanceRecord.status == "absent",
                  AttendanceRecord.date >= start,
                  AttendanceRecord.date <= end).count()) - len({
                      r.date for r in AttendanceRecord.query.filter(
                          AttendanceRecord.user_id == user_id,
                          AttendanceRecord.status == "absent",
                          AttendanceRecord.date >= start,
                          AttendanceRecord.date <= end).all()
                      if r.date in covered})
    return max(n, 0)


def fixed_earnings_total(user_id, as_of):
    """Sum of current fixed earnings assignments; falls back to User.monthly_salary."""
    total = 0.0
    for _code, (row, amt) in eng.assignments_as_of(user_id, as_of).items():
        comp = row.component
        if comp and comp.kind == "earning" and comp.is_fixed:
            total += amt or 0.0
    if total > 0:
        return round(total, 2)
    u = User.query.get(user_id)
    return round(u.monthly_salary or 0.0, 2) if u else 0.0


def calculate_lines(st):
    """Compute the settlement breakdown lines for a settlement in draft.

    Returns (lines, total_payable, total_recoverable, net). Amounts 2dp.
    Does not commit — caller commits.
    """
    u = User.query.get(st.user_id)
    if not u or not st.last_working_day:
        raise ValueError("Employee and last working day are required.")
    lwd = st.last_working_day
    basic = eng.basic_salary(st.user_id, lwd)
    basis = "resignation" if st.separation_type == "Resignation" else "employer"
    lines = []

    # 1. End-of-service gratuity (reuses the statutory calculator, Art. 51).
    # Unpaid absence days are excluded from the service period.
    unpaid_n = unpaid_absence_days(st.user_id, u.joining_date, lwd)
    st.unpaid_days_used = unpaid_n
    g = uae.gratuity(u.joining_date, lwd, basic, basis, unpaid_days=unpaid_n)
    label = (f"End-of-service gratuity \u2014 {g['effective_service_days']} service "
             f"days ({g['years_of_service']} yrs)")
    if unpaid_n > 0:
        lines.append({
            "label": f"Note: {unpaid_n:.0f} unpaid absence day(s) excluded from "
                     f"gratuity service (Art. 51)",
            "kind": "info", "amount": 0.0,
            "note": "Approved unpaid leave + unexcused absences"})
    lines.append({
        "label": label,
        "kind": "payable", "amount": g["amount"],
        "note": "UAE Labour Law, Art. 51 — full entitlement on resignation after "
                "1 year (1980-law scale repealed)"})

    # 2. Annual leave encashment: remaining balance x basic/30.
    lb = LeaveBalance.query.filter_by(user_id=st.user_id,
                                      leave_type="annual").first()
    remaining = max((lb.entitled - lb.used) if lb else 0.0, 0.0)
    daily = (basic or 0.0) / 30.0
    lines.append({
        "label": f"Annual leave encashment \u2014 {remaining:.2f} day(s) x AED {daily:.2f}/day",
        "kind": "payable", "amount": round(remaining * daily, 2),
        "note": "Annual balance remaining x (basic / 30)"})

    # 3. Final month salary, prorated on payable days in the LWD month.
    total_fixed = fixed_earnings_total(st.user_id, lwd)
    payable_days = lwd.day
    final_month = round(total_fixed * payable_days / 30.0, 2)
    lines.append({
        "label": f"Final month salary \u2014 {lwd.strftime('%b %Y')} "
                 f"({payable_days}/30 days)",
        "kind": "payable", "amount": final_month,
        "note": f"Fixed earnings AED {total_fixed:.2f} prorated"})

    # 4. Outstanding loans / advances (recoverable).
    loans = Loan.query.filter_by(user_id=st.user_id, status="active").all()
    loan_total = round(sum(l.balance or 0.0 for l in loans), 2)
    if loan_total > 0:
        lines.append({
            "label": f"Outstanding loan/advance balance ({len(loans)} record(s))",
            "kind": "recoverable", "amount": loan_total,
            "note": "Pending loan recovery"})

    # 5. Notice pay.
    if (st.notice_days or 0) > 0 and st.notice_direction != "none":
        amt = round(total_fixed * (st.notice_days or 0) / 30.0, 2)
        if st.notice_direction == "company_pays":
            kind, who = "payable", "Company pays employee"
        else:
            kind, who = "recoverable", "Employee owes company"
        lines.append({
            "label": f"Notice pay \u2014 {st.notice_days} day(s) ({who})",
            "kind": kind, "amount": amt,
            "note": f"AED {total_fixed:.2f} / 30 x {st.notice_days}"})

    # 6. Repatriation ticket (structured option).
    ticket = st.ticket_option or "none"
    if ticket == "cash" and (st.ticket_amount or 0) > 0:
        lines.append({"label": "Repatriation ticket (cash in lieu)",
                      "kind": "payable", "amount": round(st.ticket_amount, 2),
                      "note": ""})
    elif ticket == "booked":
        lines.append({"label": "Repatriation ticket: company-booked",
                      "kind": "info", "amount": 0.0,
                      "note": "Ticket arranged by the company — no cash line"})
    elif ticket == "na":
        lines.append({"label": "Repatriation ticket: not applicable",
                      "kind": "info", "amount": 0.0,
                      "note": ""})

    # 7. Manual adjustments (entered before Calculate).
    for m in (SettlementLine.query.filter_by(settlement_id=st.id, is_manual=True)
                                  .order_by(SettlementLine.id).all()):
        lines.append({"label": f"{m.label} (manual adjustment)",
                      "kind": m.kind, "amount": round(m.amount or 0.0, 2),
                      "note": m.note or ""})

    payable = round(sum(l["amount"] for l in lines if l["kind"] == "payable"), 2)
    recoverable = round(sum(l["amount"] for l in lines if l["kind"] == "recoverable"), 2)
    return lines, payable, recoverable, round(payable - recoverable, 2)


def _prefill(user_id):
    """Prefill last working day / type / reason from user or separation records."""
    lwd = sep_type = reason = sep_rec_id = None
    u = User.query.get(user_id)
    if u and u.separation_date:
        lwd, sep_type = u.separation_date, u.separation_type or "Resignation"
    rec = (SeparationRecord.query.filter_by(employee_code=user_id)
           .order_by(SeparationRecord.separation_date.desc()).first())
    if rec and rec.separation_date:
        if not lwd:
            lwd = rec.separation_date
        sep_type = sep_type or rec.separation_type or "Resignation"
        reason = rec.reason
        sep_rec_id = rec.id
    return lwd, sep_type or "Resignation", reason, sep_rec_id


@settlement_bp.route("/settlements")
@login_required
@hr_required
def settlements():
    rows = (FinalSettlement.query.order_by(FinalSettlement.created_at.desc()).all())
    users = {u.id: u for u in User.query.all()}
    return render_template("hr_settlements.html", rows=rows, users=users)


@settlement_bp.route("/settlements/new", methods=["GET", "POST"])
@login_required
@hr_required
def new():
    uid = (request.values.get("user_id") or "").strip()
    if request.method == "POST":
        f = request.form
        uid = (f.get("user_id") or "").strip()
        lwd = date_parse(f.get("last_working_day"))
        u = User.query.get(uid) if uid else None
        if not u or not lwd:
            flash("Employee and last working day are required.", "error")
        else:
            existing = (FinalSettlement.query
                        .filter(FinalSettlement.user_id == uid,
                                FinalSettlement.status != "paid")
                        .order_by(FinalSettlement.id.desc()).first())
            if existing:
                flash("An open settlement already exists for this employee.", "error")
                return redirect(url_for("settlement.detail", sid=existing.id))
            try:
                notice = max(0, int(f.get("notice_days") or 0))
            except ValueError:
                notice = 0
            sep_type = f.get("separation_type") or "Resignation"
            direction = f.get("notice_direction") or "none"
            if direction not in ("company_pays", "employee_pays", "none"):
                direction = "none"
            ticket = f.get("ticket_option") or "none"
            if ticket not in ("none", "cash", "booked", "na"):
                ticket = "none"
            try:
                ticket_amt = max(0.0, float(f.get("ticket_amount") or 0))
            except ValueError:
                ticket_amt = 0.0
            _, _, _, sep_rec_id = _prefill(uid)
            st = FinalSettlement(
                user_id=uid, separation_record_id=sep_rec_id,
                last_working_day=lwd, separation_type=sep_type,
                reason=(f.get("reason") or "").strip() or None,
                notice_days=notice, notice_direction=direction,
                ticket_option=ticket, ticket_amount=round(ticket_amt, 2),
                status="draft", created_by=current_user.id)
            db.session.add(st)
            record("settlement_created",
                   f"{uid} {sep_type} LWD {lwd.isoformat()}",
                   entity="settlement", entity_id=uid)
            db.session.commit()
            flash("Draft settlement created. Add manual adjustments, then Calculate.",
                  "success")
            return redirect(url_for("settlement.detail", sid=st.id))
    lwd, sep_type, reason, _ = _prefill(uid) if uid else (None, "Resignation", None, None)
    users = (User.query.filter(User.is_active.is_(True))
             .order_by(User.name).all())
    return render_template("hr_settlement_new.html", users=users, uid=uid,
                           lwd=lwd, sep_type=sep_type, reason=reason,
                           types=SEPARATION_TYPES)


def date_parse(s):
    s = (s or "").strip()
    try:
        from datetime import date as _d
        return _d.fromisoformat(s) if s else None
    except ValueError:
        return None


@settlement_bp.route("/settlements/<int:sid>")
@login_required
@hr_required
def detail(sid):
    st = FinalSettlement.query.get_or_404(sid)
    u = User.query.get(st.user_id)
    record("settlement_viewed", f"{st.user_id} ({st.status})",
           entity="settlement", entity_id=st.id)
    db.session.commit()
    manual = (SettlementLine.query.filter_by(settlement_id=st.id, is_manual=True)
                                  .order_by(SettlementLine.id).all())
    assets_pending = AssetCustody.query.filter_by(user_id=st.user_id)\
        .filter(AssetCustody.status == "Issued",
                AssetCustody.returned_date.is_(None)).all()
    ppe_pending = PPEIssue.query.filter_by(user_id=st.user_id)\
        .filter(PPEIssue.returned_date.is_(None)).all()
    leave_pending = (LeaveRequest.query
                     .filter_by(user_id=st.user_id, status="pending").count())
    # v3.6: activity timeline for the settlement.
    from app.models import AuditLog
    logs = AuditLog.query.filter_by(entity="settlement",
                                    entity_id=str(st.id))\
        .order_by(AuditLog.created_at.desc()).limit(40).all()
    return render_template(
        "hr_settlement_detail.html", st=st, u=u, manual=manual,
        assets_pending=assets_pending, ppe_pending=ppe_pending,
        leave_pending=leave_pending, company_name=app_config.COMPANY_NAME,
        logs=logs)


@settlement_bp.route("/settlements/<int:sid>/calculate", methods=["POST"])
@login_required
@hr_required
def calculate(sid):
    st = FinalSettlement.query.get_or_404(sid)
    if st.status != "draft":
        flash("Only a draft settlement can be calculated. "
              "Reopen it to draft first.", "error")
    else:
        try:
            lines, payable, recoverable, net = calculate_lines(st)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("settlement.detail", sid=sid))
        st.lines_json = json.dumps(lines)
        st.total_payable, st.total_recoverable, st.net = payable, recoverable, net
        st.status = "calculated"
        record("settlement_calculated",
               f"{st.user_id} payable {payable:.2f} recoverable {recoverable:.2f} "
               f"net {net:.2f}", entity="settlement", entity_id=st.id)
        db.session.commit()
        flash("Settlement calculated and frozen.", "success")
    return redirect(url_for("settlement.detail", sid=sid))


@settlement_bp.route("/settlements/<int:sid>/reopen", methods=["POST"])
@login_required
@hr_required
def reopen(sid):
    st = FinalSettlement.query.get_or_404(sid)
    if st.status != "calculated":
        flash("Only a calculated (not yet approved) settlement can be reopened.",
              "error")
    elif not current_user.is_admin:
        flash("Only an admin can reopen a settlement.", "error")
    else:
        st.status = "draft"
        record("settlement_reopened", st.user_id, entity="settlement",
               entity_id=st.id)
        db.session.commit()
        flash("Settlement reopened to draft.", "success")
    return redirect(url_for("settlement.detail", sid=sid))


@settlement_bp.route("/settlements/<int:sid>/approve", methods=["POST"])
@login_required
@hr_required
def approve(sid):
    st = FinalSettlement.query.get_or_404(sid)
    if st.status != "calculated":
        flash("Only a calculated settlement can be approved.", "error")
    elif st.created_by == current_user.id:
        flash("Segregation of duties: the settlement creator cannot approve it. "
              "Another authorised user must approve.", "error")
    else:
        st.status = "approved"
        st.approved_by = current_user.id
        st.approved_at = datetime.utcnow()
        record("settlement_approved",
               f"{st.user_id} net {st.net:.2f} approved by {current_user.id}",
               entity="settlement", entity_id=st.id)
        db.session.commit()
        flash("Settlement approved.", "success")
    return redirect(url_for("settlement.detail", sid=sid))


@settlement_bp.route("/settlements/<int:sid>/mark-disbursed", methods=["POST"])
@login_required
@hr_required
def mark_disbursed(sid):
    """Gate 1 — payment: record disbursement with a payment proof.

    Only from 'approved'. Requires a payment proof upload (bank transfer
    advice / WPS confirmation / cheque copy) plus a payment reference.
    Disbursement stops the Art. 53 overdue clock; the signed MOHRE
    cancellation / EOS receipt (gate 2) still gates Mark Paid.
    """
    import os
    from app.files import valid_document, store_file, upload_root
    st = FinalSettlement.query.get_or_404(sid)
    if st.status != "approved":
        flash("Only an approved settlement can be marked disbursed.", "error")
        return redirect(url_for("settlement.detail", sid=sid))
    payment_ref = (request.form.get("payment_ref") or "").strip()[:80]
    file = request.files.get("payment_proof")
    if not payment_ref:
        flash("A payment reference is required.", "error")
        return redirect(url_for("settlement.detail", sid=sid))
    if not file or not file.filename:
        flash("Upload a payment proof (bank transfer advice, WPS "
              "confirmation, or cheque copy).", "error")
        return redirect(url_for("settlement.detail", sid=sid))
    data = file.read()
    kind, err = valid_document(data, file.filename)
    if err:
        flash(err, "error")
        return redirect(url_for("settlement.detail", sid=sid))
    if st.payment_doc_path:
        try:
            os.remove(os.path.join(upload_root(), "settlement_payments",
                                   os.path.basename(st.payment_doc_path)))
        except OSError:
            pass
    st.payment_doc_path = store_file(
        data, "settlement_payments", "jpg" if kind == "jpg" else kind)
    st.payment_ref = payment_ref
    st.status = "disbursed"
    st.disbursed_at = datetime.utcnow()
    st.disbursed_by_id = current_user.id
    record("settlement_disbursed",
           f"{st.user_id} net {st.net:.2f} paid via ref {payment_ref}",
           entity="settlement", entity_id=st.id)
    db.session.commit()
    flash("Settlement marked as disbursed. Upload the signed cancellation "
          "receipt to close it.", "success")
    return redirect(url_for("settlement.detail", sid=sid))


@settlement_bp.route("/settlements/<int:sid>/payment-proof", methods=["GET"])
@login_required
@hr_required
def view_payment(sid):
    """Serve the payment proof (admin/salary-permission, audited)."""
    import os
    from app.files import read_file
    from flask import send_file
    import io
    st = FinalSettlement.query.get_or_404(sid)
    if not st.payment_doc_path:
        abort(404)
    data = read_file("settlement_payments",
                     os.path.basename(st.payment_doc_path))
    if not data:
        abort(404)
    record("settlement_payment_proof_viewed", f"{st.user_id}: payment proof",
           entity="settlement", entity_id=st.id)
    db.session.commit()
    ext = st.payment_doc_path.rsplit(".", 1)[-1].lower()
    mime = {"pdf": "application/pdf", "png": "image/png"}.get(ext, "image/jpeg")
    return send_file(io.BytesIO(data), mimetype=mime)


@settlement_bp.route("/settlements/<int:sid>/mark-paid", methods=["POST"])
@login_required
@hr_required
def mark_paid(sid):
    st = FinalSettlement.query.get_or_404(sid)
    if st.status != "disbursed":
        flash("Only a disbursed settlement can be marked paid — disburse the "
              "payment first.", "error")
    elif not st.cancellation_doc_path:
        flash("Upload the signed MOHRE cancellation / EOS receipt before marking "
              "this settlement as paid.", "error")
    else:
        st.status = "paid"
        st.paid_at = datetime.utcnow()
        st.paid_by = current_user.id
        record("settlement_paid", f"{st.user_id} net {st.net:.2f}",
               entity="settlement", entity_id=st.id)
        db.session.commit()
        flash("Settlement marked as paid.", "success")
    return redirect(url_for("settlement.detail", sid=sid))


@settlement_bp.route("/settlements/<int:sid>/receipt", methods=["POST"])
@login_required
@hr_required
def upload_receipt(sid):
    """Upload the signed MOHRE cancellation / EOS receipt (required for Paid)."""
    import os
    from app.files import valid_document, store_file, upload_root
    st = FinalSettlement.query.get_or_404(sid)
    if st.status not in ("approved", "calculated", "disbursed"):
        flash("A receipt can only be attached to a calculated/approved/disbursed settlement.",
              "error")
        return redirect(url_for("settlement.detail", sid=sid))
    file = request.files.get("receipt")
    if not file or not file.filename:
        flash("Choose a file to upload.", "error")
        return redirect(url_for("settlement.detail", sid=sid))
    data = file.read()
    kind, err = valid_document(data, file.filename)
    if err:
        flash(err, "error")
        return redirect(url_for("settlement.detail", sid=sid))
    # Remove the previous receipt file if one was replaced.
    if st.cancellation_doc_path:
        try:
            os.remove(os.path.join(upload_root(), "settlement_receipts",
                                   os.path.basename(st.cancellation_doc_path)))
        except OSError:
            pass
    st.cancellation_doc_path = store_file(
        data, "settlement_receipts", "jpg" if kind == "jpg" else kind)
    record("settlement_receipt_uploaded", f"{st.user_id}: cancellation receipt",
           entity="settlement", entity_id=st.id)
    db.session.commit()
    flash("Signed cancellation receipt uploaded.", "success")
    return redirect(url_for("settlement.detail", sid=sid))


@settlement_bp.route("/settlements/<int:sid>/receipt", methods=["GET"])
@login_required
@hr_required
def view_receipt(sid):
    """Serve the signed cancellation receipt (admin/salary-permission, audited)."""
    import os
    from app.files import read_file
    from flask import send_file
    import io
    st = FinalSettlement.query.get_or_404(sid)
    if not st.cancellation_doc_path:
        abort(404)
    data = read_file("settlement_receipts",
                     os.path.basename(st.cancellation_doc_path))
    if not data:
        abort(404)
    record("settlement_receipt_viewed", f"{st.user_id}: cancellation receipt",
           entity="settlement", entity_id=st.id)
    db.session.commit()
    ext = st.cancellation_doc_path.rsplit(".", 1)[-1].lower()
    mime = {"pdf": "application/pdf", "png": "image/png"}.get(ext, "image/jpeg")
    return send_file(io.BytesIO(data), mimetype=mime)


@settlement_bp.route("/settlements/<int:sid>/adjustments", methods=["POST"])
@login_required
@hr_required
def add_adjustment(sid):
    st = FinalSettlement.query.get_or_404(sid)
    if st.status != "draft":
        flash("Manual adjustments can only be added while the settlement is a draft.",
              "error")
        return redirect(url_for("settlement.detail", sid=sid))
    f = request.form
    label = (f.get("label") or "").strip()
    kind = f.get("kind") or "payable"
    try:
        amount = float(f.get("amount") or 0)
    except ValueError:
        amount = None
    if not label or amount is None or kind not in ("payable", "recoverable"):
        flash("Label, kind and a valid amount are required.", "error")
    else:
        db.session.add(SettlementLine(
            settlement_id=st.id, label=label, kind=kind, amount=round(amount, 2),
            is_manual=True, note=(f.get("note") or "").strip() or None,
            created_by=current_user.id))
        record("settlement_adjustment_added", f"{st.user_id}: {label} {amount:.2f}",
               entity="settlement", entity_id=st.id)
        db.session.commit()
        flash("Manual adjustment added.", "success")
    return redirect(url_for("settlement.detail", sid=sid))


@settlement_bp.route("/settlements/<int:sid>/adjustments/<int:lid>/delete",
                     methods=["POST"])
@login_required
@hr_required
def delete_adjustment(sid, lid):
    st = FinalSettlement.query.get_or_404(sid)
    line = SettlementLine.query.get_or_404(lid)
    if st.status != "draft" or line.settlement_id != st.id:
        flash("Manual adjustments can only be removed while the settlement is a draft.",
              "error")
    else:
        record("settlement_adjustment_removed", f"{st.user_id}: {line.label}",
               entity="settlement", entity_id=st.id)
        db.session.delete(line)
        db.session.commit()
        flash("Manual adjustment removed.", "success")
    return redirect(url_for("settlement.detail", sid=sid))
