"""Business rules of براءة الذمة, ported from the Apps Script functions
createForm, submitDepartmentResponse, checkIfFormComplete,
sendCompletionEmails, sendReminder, cancelForm, closeForm, sendToAccounting
and settleFinanceForm.

Each function commits its data change first, then sends emails, and returns
the email results so the screen can report any failure (and every attempt
is in email_logs)."""
from datetime import timedelta, date

from flask import current_app, g

from extensions import db
from models import (ClearanceForm, ClearanceResponse, ActionToken, Department)
from models.core import utcnow
from utils.audit import audit
from utils.helpers import short_code, new_token, hash_token, split_emails, is_valid_email
from utils.mail import send_email

SIGN_TOKEN_DAYS = 30


class ClearanceError(Exception):
    pass


class MailReport:
    """Collects the outcome of several emails for one action."""

    def __init__(self):
        self.items = []

    def add(self, label, result):
        self.items.append((label, result))

    @property
    def failures(self):
        return [(label, r) for label, r in self.items if not r.ok]

    @property
    def sent_count(self):
        return sum(1 for _, r in self.items if r.ok)

    def failure_text(self):
        return '، '.join(f'{label} ({r.error})' for label, r in self.failures)


def _special_department(dtype):
    return Department.query.filter_by(dept_type=dtype, is_active=True).first()


def _unique_code():
    year = date.today().year
    for _ in range(20):
        code = f'CLR-{year}-{short_code()}'
        if not ClearanceForm.query.filter_by(code=code).first():
            return code
    raise ClearanceError('تعذر توليد رقم مرجع فريد')


def _base_url():
    base = current_app.config.get('BASE_URL')
    if not base:
        raise ClearanceError('BASE_URL غير مضبوط؛ لا يمكن بناء روابط البريد')
    return base


def issue_sign_token(form, dept):
    """New one-time link for (form, department); older unused links for the
    same pair stop working."""
    now = utcnow()
    ActionToken.query.filter_by(purpose='clearance_sign', form_id=form.id,
                                department_id=dept.id, used_at=None) \
        .update({'used_at': now}, synchronize_session=False)
    token = new_token()
    db.session.add(ActionToken(purpose='clearance_sign', token_hash=hash_token(token),
                               form_id=form.id, department_id=dept.id,
                               expires_at=now + timedelta(days=SIGN_TOKEN_DAYS)))
    return f'{_base_url()}/clearance/c/{token}'


def validate_form_data(data):
    errors = []
    name = (data.get('employee_name') or '').strip()
    email = (data.get('employee_email') or '').strip()
    resign = (data.get('resign_date') or '').strip()
    if not name:
        errors.append('اسم الموظف مطلوب')
    if email and not is_valid_email(email):
        errors.append('بريد الموظف غير صالح')
    resign_date = None
    if resign:
        try:
            resign_date = date.fromisoformat(resign)
        except ValueError:
            errors.append('تاريخ الاستقالة غير صالح')
    return errors, resign_date


def create_form(data, actor):
    """createForm(): one form + one pending response per active department,
    email to the employee and a sign request to every normal department."""
    errors, resign_date = validate_form_data(data)
    if errors:
        raise ClearanceError('، '.join(errors))
    departments = Department.query.filter_by(is_active=True) \
        .order_by(Department.sort_order, Department.name).all()
    if not departments:
        raise ClearanceError('لا توجد أقسام فعّالة؛ أضف الأقسام أولًا')

    form = ClearanceForm(
        code=_unique_code(), emp_code='EMP-' + short_code(),
        employee_name=data['employee_name'].strip(),
        employee_number=(data.get('employee_number') or '').strip() or None,
        employee_email=(data.get('employee_email') or '').strip() or None,
        employee_department=(data.get('employee_department') or '').strip() or None,
        resign_date=resign_date, status='in_progress', created_by_id=actor.id)
    if data.get('employee_user_id'):
        form.employee_user_id = int(data['employee_user_id'])
    db.session.add(form)
    db.session.flush()
    for i, d in enumerate(departments):
        db.session.add(ClearanceResponse(form_id=form.id, department_id=d.id,
                                         department_name=d.name, department_icon=d.icon,
                                         sort_order=i, status='pending'))
    links = {}
    for d in departments:
        if d.dept_type == 'normal' and d.email_list:
            links[d.id] = issue_sign_token(form, d)
    audit('clearance.created', 'clearance', form.id, code=form.code,
          employee=form.employee_name, departments=len(departments))
    db.session.commit()

    report = MailReport()
    tenant = g.tenant
    if form.employee_email:
        report.add('بريد الموظف', send_email(
            [form.employee_email], 'إشعار ببدء إجراءات براءة الذمة',
            'emails/clearance_started.html', tenant, kind='clearance_started',
            entity=('clearance', form.id), form=form))
    for d in departments:
        if d.id in links:
            report.add(d.name, send_email(
                d.email_list, f'طلب توقيع براءة ذمة: {form.employee_name}',
                'emails/clearance_sign_request.html', tenant, kind='clearance_sign_request',
                entity=('clearance', form.id), form=form, dept=d, link=links[d.id]))
    from services.notify import notify, users_with_perm
    for d in departments:
        notify(users_with_perm('clearance.sign', d.id), f'طلب توقيع براءة ذمة: {form.employee_name}',
               f'بانتظار رد قسم {d.name}', f'/clearance/{form.id}/sign/{d.id}',
               'clearance_sign', exclude_user_id=actor.id)
    return form, report


def lock_form(form_id):
    """Row lock so two departments answering at the same moment cannot both
    miss (or both trigger) the completion step."""
    return ClearanceForm.query.filter_by(id=form_id).with_for_update().first()


def record_response(form_id, department_id, status, note, user):
    """submitDepartmentResponse()."""
    if status not in ('cleared', 'held'):
        raise ClearanceError('حالة غير صالحة')
    note = (note or '').strip()
    if status == 'held' and not note:
        raise ClearanceError('اذكر سبب التعليق في الملاحظة')
    form = lock_form(form_id)
    if form is None:
        raise ClearanceError('لم يتم إيجاد الطلب')
    if form.status == 'closed':
        raise ClearanceError('الملف مقفل من المحاسبة، لا يمكن التعديل')
    if form.status == 'cancelled':
        raise ClearanceError('النموذج ملغي')
    resp = ClearanceResponse.query.filter_by(form_id=form.id, department_id=department_id).first()
    if resp is None:
        raise ClearanceError('لم يتم إيجاد الطلب')

    was_complete = form.status == 'complete'
    was_fully_cleared = was_complete and not form.has_held
    old = resp.status
    resp.status, resp.note = status, note or None
    resp.responded_at, resp.responded_by, resp.responded_by_user_id = utcnow(), user.full_name, user.id
    ActionToken.query.filter_by(purpose='clearance_sign', form_id=form.id,
                                department_id=department_id, used_at=None) \
        .update({'used_at': utcnow()}, synchronize_session=False)
    db.session.flush()
    db.session.refresh(form)

    complete_now = all(r.status != 'pending' for r in form.responses)
    notify = False
    if complete_now:
        if not was_complete:
            form.status, form.completed_at = 'complete', utcnow()
            notify = True
        elif was_fully_cleared != (not form.has_held):
            notify = True       # outcome changed after completion (e.g. held -> cleared)
    audit('clearance.responded', 'clearance', form.id, department=resp.name,
          old=old, new=status, note=note or None, completed=complete_now)
    db.session.commit()
    report = send_completion_emails(form) if notify else MailReport()
    if notify:
        from services.notify import notify as push_notice, users_with_perm
        state = 'معلقة' if form.has_held else 'خالية'
        push_notice(users_with_perm('clearance.manage'), f'اكتملت براءة ذمة: {form.employee_name}',
                    f'النتيجة: {state}', f'/clearance/{form.id}', 'clearance_complete',
                    exclude_user_id=user.id)
        if form.has_held:
            push_notice(users_with_perm('finance.settle'), f'براءة ذمة بانتظار التسوية: {form.employee_name}',
                        '، '.join(r.name for r in form.held_responses), '/clearance/finance',
                        'clearance_held', exclude_user_id=user.id)
    return form, complete_now, report


def send_completion_emails(form):
    """sendCompletionEmails(): employee + HR + Finance; content depends on
    whether any department is held."""
    tenant = g.tenant
    report = MailReport()
    held = form.held_responses
    cleared = not held
    if form.employee_email:
        report.add('بريد الموظف', send_email(
            [form.employee_email],
            'إشعار: براءة الذمة جاهزة الآن' if cleared else 'إشعار: يجب استكمال براءة الذمة',
            'emails/clearance_complete_employee.html', tenant, kind='clearance_complete',
            entity=('clearance', form.id), form=form, held=held))
    for dtype, label in (('HR', 'شؤون الموظفين'), ('Finance', 'المحاسبة')):
        d = _special_department(dtype)
        if d and d.email_list:
            report.add(label, send_email(
                d.email_list, f'إشعار نهائي: براءة ذمة {form.employee_name}',
                'emails/clearance_complete_admin.html', tenant, kind='clearance_complete',
                entity=('clearance', form.id), form=form, held=held))
        else:
            report.add(label, send_email([], f'إشعار نهائي: براءة ذمة {form.employee_name}',
                                         'emails/clearance_complete_admin.html', tenant,
                                         kind='clearance_complete', entity=('clearance', form.id),
                                         form=form, held=held))
    return report


def send_reminder(form):
    """sendReminder(): fresh sign links to every still-pending department."""
    if not form.is_open:
        raise ClearanceError('النموذج مقفل أو ملغي')
    targets = []
    for r in form.responses:
        if r.status == 'pending' and r.department and r.department.is_active and r.department.email_list:
            targets.append((r.department, issue_sign_token(form, r.department)))
    audit('clearance.reminded', 'clearance', form.id, departments=[d.name for d, _ in targets])
    db.session.commit()
    report = MailReport()
    for d, link in targets:
        report.add(d.name, send_email(
            d.email_list, 'تذكير: توقيع براءة ذمة ' + form.employee_name,
            'emails/clearance_reminder.html', g.tenant, kind='clearance_reminder',
            entity=('clearance', form.id), form=form, dept=d, link=link))
    return report


def cancel_form(form):
    if form.status == 'closed':
        raise ClearanceError('لا يمكن إلغاء نموذج مقفل')
    form.status, form.cancelled_at = 'cancelled', utcnow()
    _expire_tokens(form)
    audit('clearance.cancelled', 'clearance', form.id)
    db.session.commit()


def close_form(form):
    if form.status == 'cancelled':
        raise ClearanceError('النموذج ملغي')
    form.status, form.closed_at = 'closed', utcnow()
    _expire_tokens(form)
    audit('clearance.closed', 'clearance', form.id)
    db.session.commit()


def _expire_tokens(form):
    ActionToken.query.filter_by(form_id=form.id, used_at=None) \
        .update({'used_at': utcnow()}, synchronize_session=False)


def send_to_accounting(form, note):
    """sendToAccounting(): HR note to Finance. The note is saved even when
    the email cannot be sent; the failure is reported, not hidden."""
    note = (note or '').strip()
    if not note:
        raise ClearanceError('اكتب الملاحظة')
    form.hr_note = note
    audit('clearance.sent_to_finance', 'clearance', form.id, note=note)
    db.session.commit()
    fin = _special_department('Finance')
    report = MailReport()
    report.add('المحاسبة', send_email(
        fin.email_list if fin else [], f'ملاحظة من HR بخصوص براءة ذمة: {form.employee_name}',
        'emails/clearance_to_finance.html', g.tenant, kind='clearance_to_finance',
        entity=('clearance', form.id), form=form, note=note))
    return report


def settle(form, note, user):
    """settleFinanceForm(): close the form with the settlement note and tell HR."""
    if form.status == 'cancelled':
        raise ClearanceError('النموذج ملغي')
    if form.status == 'closed':
        raise ClearanceError('تمت التسوية مسبقًا')
    if not form.has_held:
        raise ClearanceError('لا توجد التزامات معلقة على هذا النموذج')
    note = (note or '').strip()
    form.status, form.closed_at = 'closed', utcnow()
    form.settled_at, form.settled_by_id = utcnow(), user.id
    form.finance_note = note or None
    _expire_tokens(form)
    audit('clearance.settled', 'clearance', form.id, note=note or None)
    db.session.commit()
    hr = _special_department('HR')
    report = MailReport()
    report.add('شؤون الموظفين', send_email(
        hr.email_list if hr else [], f'تسوية مالية: براءة ذمة {form.employee_name}',
        'emails/clearance_settled.html', g.tenant, kind='clearance_settled',
        entity=('clearance', form.id), form=form, note=note))
    from services.notify import notify, users_with_perm
    notify(users_with_perm('clearance.manage'), f'تمت التسوية المالية: {form.employee_name}',
           note or None, f'/clearance/{form.id}', 'clearance_settled', exclude_user_id=user.id)
    return report
