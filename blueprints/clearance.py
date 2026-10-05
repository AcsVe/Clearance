from flask import (Blueprint, render_template, request, redirect, url_for, flash, g, abort,
                   session)
from sqlalchemy import func, or_

from authz import (login_required, permission_required, current_user, can_access_department)
from extensions import db
from models import ClearanceForm, ClearanceResponse, ActionToken, Department, User, FORM_STATUS
from models.core import utcnow
from services import clearance as svc
from tenancy import tenant_bypass
from utils.helpers import hash_token

clearance_bp = Blueprint('clearance', __name__)


def _flash_report(report, ok_message):
    if report.failures:
        flash(f'{ok_message}، لكن تعذّر إرسال بعض الرسائل: {report.failure_text()}', 'warning')
    else:
        flash(ok_message, 'success')


def _form_or_404(form_id):
    form = db.session.get(ClearanceForm, form_id)
    if form is None:
        abort(404)
    return form


def _is_own_form(user, form):
    if form.employee_user_id and form.employee_user_id == user.id:
        return True
    return bool(user.email and form.employee_email
                and user.email.lower() == form.employee_email.lower())


def can_view_form(user, form):
    if user.has_perm('clearance.view_all'):
        return True
    if user.has_perm('finance.settle') and form.has_held:
        return True
    if user.has_perm('clearance.sign') and any(
            r.department_id in user.department_ids for r in form.responses):
        return True
    return user.has_perm('self.view') and _is_own_form(user, form)


# ─────────────────────────── HR: list / create / manage ───────────────────────────
@clearance_bp.route('/')
@permission_required('clearance.view_all')
def index():
    status = request.args.get('status') or ''
    q = (request.args.get('q') or '').strip()
    query = ClearanceForm.query
    if status in FORM_STATUS:
        query = query.filter(ClearanceForm.status == status)
    if q:
        like = f'%{q.lower()}%'
        query = query.filter(or_(func.lower(ClearanceForm.employee_name).like(like),
                                 func.lower(ClearanceForm.code).like(like),
                                 func.lower(func.coalesce(ClearanceForm.employee_number, '')).like(like)))
    forms = query.order_by(ClearanceForm.id.desc()).limit(300).all()   # newest first
    return render_template('clearance/list.html', forms=forms, status=status, q=q,
                           statuses=FORM_STATUS)


@clearance_bp.route('/new', methods=['GET', 'POST'])
@permission_required('clearance.create')
def new():
    data = request.form if request.method == 'POST' else {}
    error = None
    if request.method == 'POST':
        try:
            form, report = svc.create_form(request.form, g.user)
            _flash_report(report, f'تم إنشاء النموذج {form.code}')
            return redirect(url_for('clearance.detail', form_id=form.id))
        except svc.ClearanceError as e:
            db.session.rollback()
            error = str(e)
    depts = Department.query.filter_by(is_active=True).order_by(Department.sort_order,
                                                                Department.name).all()
    return render_template('clearance/new.html', data=data, error=error, departments=depts), \
        (400 if error else 200)


@clearance_bp.route('/<int:form_id>')
@login_required
def detail(form_id):
    form = _form_or_404(form_id)
    if not can_view_form(g.user, form):
        abort(403)
    return render_template('clearance/detail.html', form=form)


@clearance_bp.route('/<int:form_id>/action', methods=['POST'])
@permission_required('clearance.manage')
def action(form_id):
    form = _form_or_404(form_id)
    act = request.form.get('action')
    try:
        if act == 'remind':
            report = svc.send_reminder(form)
            if not report.items:
                flash('لا توجد أقسام بانتظار التوقيع لديها بريد', 'info')
            else:
                _flash_report(report, f'تم إرسال التذكير لـ {report.sent_count} أقسام')
        elif act == 'cancel':
            svc.cancel_form(form)
            flash('تم إلغاء النموذج', 'success')
        elif act == 'close':
            svc.close_form(form)
            flash('تم إقفال النموذج', 'success')
        elif act == 'to_finance':
            report = svc.send_to_accounting(form, request.form.get('note'))
            _flash_report(report, 'تم حفظ الملاحظة وإرسالها للمحاسبة')
        else:
            abort(400)
    except svc.ClearanceError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    return redirect(url_for('clearance.detail', form_id=form_id))


# ─────────────────────────── Department portal ───────────────────────────
@clearance_bp.route('/sign')
@permission_required('clearance.sign')
def sign_list():
    """getPendingForDepartment(): open forms where one of MY departments is
    pending or held."""
    my_depts = g.user.department_ids
    rows = []
    if my_depts:
        rows = (ClearanceResponse.query.join(ClearanceForm)
                .filter(ClearanceResponse.department_id.in_(my_depts),
                        ClearanceResponse.status.in_(('pending', 'held')),
                        ClearanceForm.status.notin_(('closed', 'cancelled')))
                .order_by(ClearanceForm.id.desc()).all())
    return render_template('clearance/sign_list.html', rows=rows)


@clearance_bp.route('/<int:form_id>/sign/<int:dept_id>', methods=['GET', 'POST'])
@permission_required('clearance.sign')
def sign(form_id, dept_id):
    if not can_access_department(g.user, dept_id, 'clearance.sign'):
        abort(403)
    form = _form_or_404(form_id)
    resp = next((r for r in form.responses if r.department_id == dept_id), None)
    if resp is None:
        abort(404)
    error = None
    if request.method == 'POST':
        try:
            form, complete, report = svc.record_response(
                form.id, dept_id, request.form.get('status'), request.form.get('note'), g.user)
            msg = 'تم حفظ الرد' + ('، واكتمل النموذج' if complete else '')
            _flash_report(report, msg)
            return redirect(url_for('clearance.sign_list'))
        except svc.ClearanceError as e:
            db.session.rollback()
            error = str(e)
            form = _form_or_404(form_id)
            resp = next(r for r in form.responses if r.department_id == dept_id)
    return render_template('clearance/sign.html', form=form, resp=resp, error=error), \
        (400 if error else 200)


@clearance_bp.route('/c/<token>', endpoint='token_link')
def token_link(token):
    """Emailed one-time link. Requires login; only a user who may sign for
    that department gets through. The token dies when the department
    responds, when a newer reminder is sent, or after 30 days."""
    th = hash_token(token)
    with tenant_bypass():
        row = ActionToken.query.filter_by(token_hash=th, purpose='clearance_sign').first()
        info = (row.tenant_id, row.form_id, row.department_id, row.used_at, row.expires_at) \
            if row else None
    if info is None:
        abort(404)
    tenant_id, form_id, dept_id, used_at, expires_at = info
    user = current_user()
    if user is None:
        return redirect(url_for('auth.login', next=request.path))
    if g.tenant is None or g.tenant.id != tenant_id:
        abort(404)
    if used_at or expires_at < utcnow():
        flash('هذا الرابط استُخدم أو انتهت صلاحيته. يمكنك الرد من بوابة القسم.', 'info')
        return redirect(url_for('clearance.sign_list') if user.has_perm('clearance.sign')
                        else url_for('main.home'))
    if not can_access_department(user, dept_id, 'clearance.sign'):
        abort(403)
    return redirect(url_for('clearance.sign', form_id=form_id, dept_id=dept_id))


# ─────────────────────────── Finance portal ───────────────────────────
@clearance_bp.route('/finance')
@permission_required('finance.settle')
def finance():
    """getFinanceForms(): pending = open with a held department; closed =
    closed with a held department (i.e. settled)."""
    held_form_ids = db.session.query(ClearanceResponse.form_id) \
        .filter(ClearanceResponse.status == 'held').distinct()
    base = ClearanceForm.query.filter(ClearanceForm.id.in_(held_form_ids))
    pending = base.filter(ClearanceForm.status.notin_(('closed', 'cancelled'))) \
        .order_by(ClearanceForm.id.desc()).all()
    closed = base.filter(ClearanceForm.status == 'closed') \
        .order_by(ClearanceForm.id.desc()).limit(200).all()
    return render_template('clearance/finance.html', pending=pending, closed=closed)


@clearance_bp.route('/<int:form_id>/settle', methods=['POST'])
@permission_required('finance.settle')
def settle(form_id):
    form = _form_or_404(form_id)
    try:
        report = svc.settle(form, request.form.get('note'), g.user)
        _flash_report(report, 'تمت التسوية وإقفال النموذج')
    except svc.ClearanceError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    return redirect(url_for('clearance.finance'))


# ─────────────────────────── Print / employee ───────────────────────────
@clearance_bp.route('/<int:form_id>/print')
@login_required
def print_form(form_id):
    form = _form_or_404(form_id)
    if not can_view_form(g.user, form):
        abort(403)
    return render_template('clearance/print.html', f=form)


@clearance_bp.route('/mine')
@login_required
def mine():
    user = g.user
    cond = [ClearanceForm.employee_user_id == user.id]
    if user.email:
        cond.append(func.lower(ClearanceForm.employee_email) == user.email.lower())
    forms = ClearanceForm.query.filter(or_(*cond)).order_by(ClearanceForm.id.desc()).all()
    return render_template('clearance/mine.html', forms=forms)
