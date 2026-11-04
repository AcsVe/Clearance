"""Clearance forms: HR list and actions, the department portal, the signing link, the finance portal and
the printable form. The rules themselves live in services/clearance.py."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort

from authz import permission_required, login_required, current_user
from extensions import db
from tenancy import get_scoped
from models import (ClearanceForm, ClearanceResponse, Department, Employee, Custody, Tenant,
                    FORM_STATUSES, RESPONSE_STATUSES)
from tenancy import tenant_bypass, set_current_tenant
from utils.audit import audit
from utils.tabular import parse_date
from services import clearance as wf
from utils.i18n import pick

clearance_bp = Blueprint('clearance', __name__)

VIEW_PERMS = ('clearance.view_all', 'clearance.create', 'clearance.manage')


def _form(form_id):
    f = get_scoped(ClearanceForm, form_id)
    if f is None:
        abort(404)
    return f


def _can_view(user, form):
    if any(user.has_perm(p) for p in VIEW_PERMS + ('finance.settle',)):
        return True
    if user.has_perm('clearance.sign'):
        mine = set(user.department_ids)
        if any(r.department_id in mine for r in form.responses):
            return True
    return bool(form.employee and form.employee.user_id == user.id)   # the employee's own form


def _may_sign(user, resp):
    """Department managers sign for their own departments; HR managers can also record a response for
    any department (e.g. a signed paper copy)."""
    if user.has_perm('clearance.manage'):
        return True
    return user.has_perm('clearance.sign') and resp.department_id in set(user.department_ids)


def _ids():
    return {int(v) for v in request.form.getlist('ids') if v.isdigit()}


# ───────────────────────────── list / create ─────────────────────────────
@clearance_bp.route('/clearance/')
@permission_required(*VIEW_PERMS)
def index():
    forms = ClearanceForm.query.order_by(ClearanceForm.id.desc()).all()
    return render_template('clearance/index.html', forms=forms, statuses=dict(FORM_STATUSES))


@clearance_bp.route('/clearance/new', methods=['GET', 'POST'])
@permission_required('clearance.create')
def new():
    depts = Department.query.filter_by(is_active=True).order_by(Department.sort_order, Department.name).all()
    employees = Employee.query.filter_by(is_active=True).order_by(Employee.full_name).all()
    values, errors = dict(request.form), []
    picked = None                               # «start clearance» from an employee card
    if request.method == 'GET' and (request.args.get('employee_id') or '').isdigit():
        picked = get_scoped(Employee, int(request.args['employee_id']))
        if picked is None:
            abort(404)
        values = {'employee_id': str(picked.id), 'employee_name': picked.full_name,
                  'employee_number': picked.employee_number or '', 'employee_email': picked.email or '',
                  'department_name': picked.department.name if picked.department else '',
                  'job_title': picked.job_title or ''}
        if picked not in employees:
            employees = employees + [picked]
    elif (values.get('employee_id') or '').isdigit():
        picked = get_scoped(Employee, int(values['employee_id']))
    if request.method == 'POST':
        emp = None
        if (request.form.get('employee_id') or '').isdigit():
            emp = get_scoped(Employee, int(request.form['employee_id']))
        name = (request.form.get('employee_name') or '').strip() or (emp.full_name if emp else '')
        email = (request.form.get('employee_email') or '').strip() or (emp.email if emp else None)
        number = (request.form.get('employee_number') or '').strip() or (emp.employee_number if emp else None)
        dept_name = (request.form.get('department_name') or '').strip() or \
            (emp.department.name if emp and emp.department else None)
        job = (request.form.get('job_title') or '').strip() or (emp.job_title if emp else None)
        resign = parse_date(request.form.get('resign_date'))
        chosen = {int(v) for v in request.form.getlist('departments') if v.isdigit()}
        if not name:
            errors.append(pick('اختر الموظف من السجل أو اكتب اسمه', 'Choose the employee from the register or type their name'))
        if request.form.get('resign_date') and not resign:
            errors.append(pick('تاريخ الاستقالة غير صالح', 'The resignation date is not valid'))
        if not chosen:
            errors.append(pick('اختر قسمًا واحدًا على الأقل', 'Choose at least one department'))
        if emp and ClearanceForm.query.filter(ClearanceForm.employee_id == emp.id,
                                              ClearanceForm.status.in_(('in_progress', 'completed'))).first() \
                and request.form.get('allow_duplicate') != '1':
            errors.append(pick('لهذا الموظف نموذج مفتوح — أكّد الخيار «إنشاء نموذج آخر» إن كان ذلك مقصودًا', 'This employee has an open form — tick "Create another form" if that is intended'))
            values['dup_warning'] = '1'
        if not errors:
            try:
                form = wf.create_form(employee=emp, employee_name=name, employee_number=number,
                                      employee_email=email, department_name=dept_name, job_title=job,
                                      resign_date=resign, department_ids=chosen,
                                      created_by=current_user().full_name)
            except wf.WorkflowError as exc:
                errors.append(str(exc))
            else:
                flash(pick(f'أُنشئ النموذج {form.ref_code} وأُرسلت روابط التوقيع إلى الأقسام', f'Form {form.ref_code} was created and the signing links were sent to the departments'), 'success')
                return redirect(url_for('clearance.view', form_id=form.id))
    open_custody = Custody.query.filter_by(employee_id=picked.id, status='handed').order_by(Custody.handed_at).all() \
        if picked else []
    open_forms = ClearanceForm.query.filter(ClearanceForm.employee_id == picked.id,
                                            ClearanceForm.status.in_(('in_progress', 'completed'))).all() if picked else []
    return render_template('clearance/new.html', departments=depts, employees=employees, v=values, picked=picked,
                           open_custody=open_custody, open_forms=open_forms,
                           errors=errors), (400 if errors else 200)


@clearance_bp.route('/clearance/new-many', methods=['POST'])
@permission_required('clearance.create')
def new_many():
    """Clearance for several employees at once (from the employee list): the same departments and resignation
    date for all. First call: the choice page; second call (step=create): the forms."""
    ids = sorted(_ids())
    people = Employee.query.filter(Employee.id.in_(ids or [0])).order_by(Employee.full_name).all()
    if not people:
        flash(pick('حدّد موظفاً واحداً على الأقل', 'Select at least one employee'), 'warning')
        return redirect(url_for('employees.index'))
    depts = Department.query.filter_by(is_active=True).order_by(Department.sort_order, Department.name).all()
    custody = {}
    for c in Custody.query.filter(Custody.employee_id.in_(ids), Custody.status == 'handed').all():
        custody[c.employee_id] = custody.get(c.employee_id, 0) + 1
    busy = {f.employee_id for f in ClearanceForm.query.filter(ClearanceForm.employee_id.in_(ids),
                                                              ClearanceForm.status.in_(('in_progress', 'completed')))}
    errors = []
    if request.form.get('step') == 'create':
        chosen = {int(v) for v in request.form.getlist('departments') if v.isdigit()}
        resign = parse_date(request.form.get('resign_date'))
        if request.form.get('resign_date') and not resign:
            errors.append(pick('تاريخ الاستقالة غير صالح', 'The resignation date is not valid'))
        if not chosen:
            errors.append(pick('اختر قسمًا واحدًا على الأقل', 'Choose at least one department'))
        if not errors:
            made, skipped = [], []
            again = request.form.get('allow_duplicate') == '1'
            for emp in people:
                if emp.id in busy and not again:
                    skipped.append(pick(f'{emp.full_name} (له نموذج مفتوح)', f'{emp.full_name} (has an open form)'))
                    continue
                try:
                    form = wf.create_form(employee=emp, employee_name=emp.full_name,
                                          employee_number=emp.employee_number, employee_email=emp.email,
                                          department_name=emp.department.name if emp.department else None,
                                          job_title=emp.job_title, resign_date=resign, department_ids=chosen,
                                          created_by=current_user().full_name)
                except wf.WorkflowError as exc:
                    skipped.append(f'{emp.full_name} ({exc})')
                    continue
                made.append(form.ref_code)
            msg = pick(f'النماذج المنشأة: {len(made)}، وأُرسلت روابط التوقيع إلى الأقسام', f'Forms created: {len(made)}; the signing links were sent to the departments')
            if skipped:
                msg += pick(' — تُرك: ', ' — skipped: ') + pick('، ', ', ').join(skipped[:10])
            flash(msg, 'success' if made else 'warning')
            return redirect(url_for('clearance.index'))
    return render_template('clearance/new_many.html', people=people, departments=depts, custody=custody,
                           busy=busy, errors=errors, v=request.form), (400 if errors else 200)


@clearance_bp.route('/clearance/<int:form_id>')
@login_required
def view(form_id):
    form = _form(form_id)
    user = current_user()
    if not _can_view(user, form):
        abort(403)
    custody = []
    if form.employee_id:
        custody = Custody.query.filter_by(employee_id=form.employee_id).order_by(Custody.status, Custody.id).all()
    return render_template('clearance/view.html', f=form, custody=custody,
                           may_sign={r.id: _may_sign(user, r) for r in form.responses},
                           statuses=RESPONSE_STATUSES)


@clearance_bp.route('/clearance/<int:form_id>/action', methods=['POST'])
@permission_required('clearance.manage')
def action(form_id):
    form = _form(form_id)
    act = request.form.get('action')
    try:
        if act == 'remind':
            n = wf.remind(form)
            flash(pick(f'تم إرسال التذكير إلى {n} من الأقسام', f'A reminder was sent to {n} department(s)'), 'success' if n else 'warning')
        elif act in ('cancelled', 'closed', 'reopen'):
            wf.set_status(form, act)
            flash({'cancelled': pick('أُلغي النموذج', 'The form was cancelled'), 'closed': pick('أُقفل النموذج', 'The form was closed'), 'reopen': pick('أُعيد فتح النموذج', 'The form was reopened')}[act],
                  'success')
        elif act == 'to_finance':
            res = wf.send_to_finance(form, request.form.get('note'))
            flash(pick('تم الإرسال إلى المحاسبة', 'Sent to Finance') if res else pick('حُفظت الملاحظة وتعذّر إرسال البريد — راجع سجل البريد', 'The note was saved but the email could not be sent — see the mail log'),
                  'success' if res else 'warning')
        else:
            abort(400)
    except wf.WorkflowError as exc:
        flash(str(exc), 'danger')
    return redirect(request.referrer or url_for('clearance.view', form_id=form.id))


@clearance_bp.route('/clearance/bulk', methods=['POST'])
@permission_required('clearance.manage')
def bulk():
    act = request.form.get('action')
    if act not in ('remind', 'cancelled', 'closed', 'delete'):
        abort(400)
    done, skipped = 0, []
    for form in ClearanceForm.query.filter(ClearanceForm.id.in_(_ids())).all():
        try:
            if act == 'remind':
                wf.remind(form)
            elif act == 'delete':
                answered = any(r.status != 'pending' for r in form.responses)
                if form.status != 'cancelled' and answered:
                    skipped.append(pick(f'{form.ref_code} (عليه توقيعات — ألغِه أولاً)', f'{form.ref_code} (it has signatures — cancel it first)'))
                    continue
                audit('clearance.deleted', 'clearance', form.id, ref=form.ref_code, employee=form.employee_name)
                db.session.delete(form)
                db.session.commit()
            else:
                wf.set_status(form, act)
            done += 1
        except wf.WorkflowError as exc:
            skipped.append(f'{form.ref_code} ({exc})')
    verb = {'remind': pick('ذُكِّر', 'Reminded'), 'cancelled': pick('أُلغي', 'Cancelled'), 'closed': pick('أُقفل', 'Closed'), 'delete': pick('حُذف', 'Deleted')}[act]
    flash(f'{verb}: {done}' + ((pick(' — تُرك: ', ' — skipped: ') + pick('، ', ', ').join(skipped[:10])) if skipped else ''),
          'success' if done else 'warning')
    return redirect(url_for('clearance.index'))


@clearance_bp.route('/clearance/<int:form_id>/print')
@login_required
def print_form(form_id):
    form = _form(form_id)
    if not _can_view(current_user(), form):
        abort(403)
    return render_template('clearance/print.html', f=form)


# ───────────────────────────── department portal ─────────────────────────────
@clearance_bp.route('/departments/portal')
@permission_required('clearance.sign', 'clearance.manage')
def portal():
    user = current_user()
    if user.has_perm('clearance.manage') and request.args.get('all') == '1':
        dept_ids = [d.id for d in Department.query.all()]
    else:
        dept_ids = list(user.department_ids)
    rows = []
    if dept_ids:
        rows = (ClearanceResponse.query.join(ClearanceForm)
                .filter(ClearanceResponse.department_id.in_(dept_ids),
                        ClearanceForm.status.in_(('in_progress', 'completed')))
                .order_by(ClearanceForm.id.desc()).all())
    waiting = [r for r in rows if r.status in ('pending', 'held')]
    answered = [r for r in rows if r.status == 'cleared']
    return render_template('clearance/portal.html', waiting=waiting, answered=answered,
                           departments=Department.query.filter(Department.id.in_(dept_ids)).all() if dept_ids else [])


@clearance_bp.route('/clearance/respond/<int:resp_id>', methods=['POST'])
@login_required
def respond(resp_id):
    resp = get_scoped(ClearanceResponse, resp_id) or abort(404)
    user = current_user()
    if not _may_sign(user, resp):
        abort(403)
    try:
        done = wf.respond(resp, request.form.get('status'), request.form.get('note'),
                          request.form.get('by_name') or user.full_name, by_user=user)
        flash(pick('تم حفظ التوقيع', 'The signature was saved') + (pick(' — اكتملت جميع التوقيعات وأُرسلت الإشعارات', ' — all signatures are complete and the notifications were sent') if done else ''), 'success')
    except wf.WorkflowError as exc:
        flash(str(exc), 'danger')
    return redirect(request.form.get('next') or url_for('clearance.portal'))


# ───────────────────────────── signing link (no login) ─────────────────────────────
def _load_token(token):
    if not token or len(token) > 80:
        return None
    with tenant_bypass():
        resp = ClearanceResponse.query.filter_by(token=token).first()
        tid = resp.tenant_id if resp else None
    if not resp:
        return None
    tenant = db.session.get(Tenant, tid)
    if not tenant or not tenant.is_active:
        return None
    set_current_tenant(tid)
    g.tenant = tenant
    return ClearanceResponse.query.filter_by(token=token).first()


@clearance_bp.route('/sign/<token>', methods=['GET', 'POST'])
def sign(token):
    resp = _load_token(token)
    if resp is None:
        return render_template('clearance/sign.html', resp=None), 404
    form = resp.form
    if request.method == 'POST':
        try:
            wf.respond(resp, request.form.get('status'), request.form.get('note'), request.form.get('by_name'),
                       by_user=current_user())
            flash(pick('شكرًا، تم حفظ ردّ القسم', 'Thank you, the department reply was saved'), 'success')
        except wf.WorkflowError as exc:
            flash(str(exc), 'danger')
        return redirect(url_for('clearance.sign', token=token))
    custody = []
    if form.employee_id:
        custody = Custody.query.filter_by(employee_id=form.employee_id, department_id=resp.department_id,
                                          status='handed').all()
    return render_template('clearance/sign.html', resp=resp, f=form, custody=custody)


# ───────────────────────────── finance portal ─────────────────────────────
@clearance_bp.route('/finance')
@permission_required('finance.settle')
def finance():
    forms = ClearanceForm.query.filter(ClearanceForm.status != 'cancelled').order_by(ClearanceForm.id.desc()).all()
    pending = [f for f in forms if f.status in ('in_progress', 'completed') and (f.held or f.sent_to_finance_at)]
    settled = [f for f in forms if f.settled]
    return render_template('clearance/finance.html', pending=pending, settled=settled)


@clearance_bp.route('/finance/<int:form_id>/settle', methods=['POST'])
@permission_required('finance.settle')
def settle(form_id):
    form = _form(form_id)
    try:
        res = wf.settle(form, request.form.get('note'), current_user().full_name)
        flash(pick('تمت التسوية', 'Settled') + (pick(' وإشعار شؤون الموظفين', ' and HR was notified') if res else ''), 'success')
        if res is not None and not res:
            flash(pick('تعذّر إرسال الإشعار — راجع سجل البريد', 'The notification could not be sent — see the mail log'), 'warning')
    except wf.WorkflowError as exc:
        flash(str(exc), 'danger')
    return redirect(url_for('clearance.finance'))
