"""The clearance workflow, as in the original Apps Script (createForm, submitDepartmentResponse,
sendCompletionEmails, sendReminder, cancel/close, sendToAccounting, settleFinanceForm) — kept separate
from the web routes so the importer and the scheduler use the same rules."""
from datetime import timedelta

from flask import current_app, g, has_request_context

from extensions import db
from models import ClearanceForm, ClearanceResponse, Department, Custody, Tenant
from models.core import utcnow
from tenancy import current_tenant_id
from utils.audit import audit
from utils.helpers import short_code
from utils.mail import send_email

NOTICE = 'emails/notice.html'


class WorkflowError(Exception):
    pass


def _tenant():
    if has_request_context() and getattr(g, 'tenant', None) is not None:
        return g.tenant
    return db.session.get(Tenant, current_tenant_id())


def _base():
    return (current_app.config.get('BASE_URL') or '').rstrip('/')


def sign_link(resp):
    return f'{_base()}/sign/{resp.token}'


def dept_of(kind):
    """The active HR or Finance department."""
    return Department.query.filter_by(dept_type=kind, is_active=True).first()


def new_ref_code():
    year = utcnow().year
    while True:
        code = f'CLR-{year}-{short_code(6)}'
        if not ClearanceForm.query.filter_by(ref_code=code).first():
            return code


# ───────────────────────────── create ─────────────────────────────
def create_form(*, employee=None, employee_name, employee_number=None, employee_email=None, department_name=None,
                job_title=None, resign_date=None, department_ids=None, created_by=None, notify=True):
    """New form + one «قيد الانتظار» response per chosen department (default: every active department).
    Mails the employee the reference code and every normal department its signing link."""
    depts = Department.query.filter(Department.is_active.is_(True))
    if department_ids:
        depts = depts.filter(Department.id.in_(department_ids))
    depts = depts.order_by(Department.sort_order, Department.name).all()
    if not depts:
        raise WorkflowError('لا توجد أقسام فعّالة لإرسال النموذج إليها — أضف الأقسام أولاً')
    form = ClearanceForm(ref_code=new_ref_code(), employee_id=employee.id if employee else None,
                         employee_name=employee_name, employee_number=employee_number,
                         employee_email=employee_email, department_name=department_name, job_title=job_title,
                         resign_date=resign_date, status='in_progress', created_by=created_by)
    db.session.add(form)
    db.session.flush()
    for d in depts:
        form.responses.append(ClearanceResponse(department_id=d.id, status='pending'))
    db.session.flush()
    audit('clearance.created', 'clearance', form.id, ref=form.ref_code, employee=employee_name,
          departments=len(depts))
    db.session.commit()
    if notify:
        notify_created(form)
    return form


def notify_created(form):
    tenant = _tenant()
    if form.employee_email:
        send_email([form.employee_email], 'إشعار ببدء إجراءات براءة الذمة', NOTICE, tenant,
                   kind='clearance.employee_started', entity=('clearance', form.id),
                   heading='إشعار ببدء الإجراءات', greeting=form.employee_name,
                   paragraphs=['نود إعلامكم بأنه تم البدء بمعاملة براءة الذمة الخاصة بكم في المؤسسة.'],
                   code=form.ref_code, code_label='رقم المرجع الخاص بكم',
                   after=['سيتم إشعاركم فور اكتمال جميع التوقيعات والتسويات المالية.'])
    for r in form.responses:
        _mail_department(form, r, reminder=False)


def _custody_lines(form, dept_id):
    if not form.employee_id:
        return []
    rows = Custody.query.filter_by(employee_id=form.employee_id, department_id=dept_id, status='handed').all()
    return [(c.item + (f' × {c.quantity}' if c.quantity and c.quantity > 1 else ''),
             f'سُلّمت {c.handed_at}' if c.handed_at else '') for c in rows]


def _mail_department(form, resp, reminder):
    d = resp.department
    if not d or d.dept_type in ('HR', 'Finance') or not d.email_list:
        return None
    items = _custody_lines(form, d.id)
    paragraphs = [(f'تذكير بتوقيع براءة ذمة الموظف «{form.employee_name}».' if reminder else
                   f'يوجد طلب براءة ذمة بانتظار توقيعكم للموظف «{form.employee_name}».')]
    if items:
        paragraphs.append('عهد مسجّلة على الموظف لدى قسمكم ولم تُسترجع بعد:')
    return send_email(d.email_list, (f'تذكير: توقيع براءة ذمة {form.employee_name}' if reminder
                                     else f'طلب توقيع براءة ذمة: {form.employee_name}'), NOTICE, _tenant(),
                      kind='clearance.reminder' if reminder else 'clearance.sign_request',
                      entity=('clearance', form.id),
                      heading='تذكير هام' if reminder else 'طلب توقيع براءة ذمة',
                      greeting=d.manager_name or d.name, paragraphs=paragraphs, items=items, items_color='#92400e',
                      facts=[('الرقم المرجعي', form.ref_code), ('القسم', d.name)] +
                            ([('تاريخ الاستقالة', str(form.resign_date))] if form.resign_date else []),
                      button_url=sign_link(resp), button_label='✍️ فتح صفحة التوقيع',
                      after=['الرابط خاص بقسمكم لهذا النموذج فقط، ويمكن أيضًا التوقيع من «بوابة الأقسام» بعد تسجيل الدخول.'])


# ───────────────────────────── respond ─────────────────────────────
def respond(resp, status, note, by_name, by_user=None):
    form = resp.form
    if form.status == 'closed':
        raise WorkflowError('الملف مقفل من المحاسبة، لا يمكن التعديل')
    if form.status == 'cancelled':
        raise WorkflowError('هذا النموذج ملغي')
    if status not in ('cleared', 'held'):
        raise WorkflowError('اختر الحالة: خالية الذمة أو معلق')
    note = (note or '').strip()[:1000] or None
    if status == 'held' and not note:
        raise WorkflowError('اكتب سبب التعليق (ما المطلوب من الموظف) في الملاحظات')
    if not (by_name or '').strip():
        raise WorkflowError('اكتب اسم الموقّع')
    changed = resp.status != status or (resp.note or None) != note
    resp.status, resp.note = status, note
    resp.responded_at, resp.responded_by = utcnow(), by_name.strip()[:200]
    resp.responded_by_user_id = by_user.id if by_user else None
    audit('clearance.responded', 'clearance', form.id, department=resp.department.name if resp.department else None,
          status=status)
    newly_complete = form.status == 'in_progress' and not any(r.status == 'pending' for r in form.responses)
    if newly_complete:
        form.status, form.completed_at = 'completed', utcnow()
        audit('clearance.completed', 'clearance', form.id, held=len(form.held))
    db.session.commit()
    if newly_complete or (form.status == 'completed' and changed):
        send_completion(form)
    return newly_complete


def send_completion(form):
    """To the employee, HR and Finance: cleared, or the list of departments holding it."""
    tenant = _tenant()
    held = [(r.department.name if r.department else 'قسم محذوف', r.note) for r in form.held]
    if not held:
        emp = dict(subject='إشعار: براءة الذمة جاهزة الآن',
                   paragraphs=['نسعد بإعلامكم بأن براءة الذمة جاهزة الآن وهي خالية.',
                               'يمكنكم المتابعة مع إدارة شؤون الموظفين.'])
        adm = dict(paragraphs=[f'تم إصدار براءة الذمة بنجاح للموظف «{form.employee_name}» وهي خالية.'])
    else:
        emp = dict(subject='إشعار: يجب استكمال براءة الذمة',
                   paragraphs=['يرجى استكمال براءة الذمة لوجود التزامات لدى الأقسام التالية:'], items=held)
        adm = dict(paragraphs=[f'توجد التزامات معلقة للموظف «{form.employee_name}» لدى:'], items=held)
    if form.employee_email:
        send_email([form.employee_email], emp.pop('subject'), NOTICE, tenant, kind='clearance.employee_result',
                   entity=('clearance', form.id), heading='تحديث براءة الذمة', greeting=form.employee_name,
                   code=form.ref_code, **emp)
    to = [e for kind in ('HR', 'Finance') for e in (dept_of(kind).email_list if dept_of(kind) else [])]
    if to:
        send_email(to, f'إشعار نهائي: براءة ذمة {form.employee_name}', NOTICE, tenant, kind='clearance.admin_result',
                   entity=('clearance', form.id), heading='إشعار إداري', code=form.ref_code,
                   button_url=f'{_base()}/clearance/{form.id}', button_label='عرض النموذج', **adm)


# ───────────────────────────── HR actions ─────────────────────────────
def remind(form):
    if form.status not in ('in_progress', 'completed'):
        raise WorkflowError('لا يمكن التذكير لنموذج مقفل أو ملغي')
    sent = 0
    for r in form.responses:
        if r.status == 'pending':
            res = _mail_department(form, r, reminder=True)
            if res:
                sent += 1
    form.last_reminder_at = utcnow()
    audit('clearance.reminded', 'clearance', form.id, sent=sent)
    db.session.commit()
    return sent


def set_status(form, status):
    if status == 'cancelled':
        if form.status == 'closed':
            raise WorkflowError('لا يمكن إلغاء نموذج مقفل')
        form.status, form.cancelled_at = 'cancelled', utcnow()
    elif status == 'closed':
        if form.status == 'cancelled':
            raise WorkflowError('لا يمكن إقفال نموذج ملغي')
        form.status, form.closed_at = 'closed', utcnow()
    elif status == 'reopen':
        if form.settled:
            raise WorkflowError('لا يمكن إعادة فتح نموذج تمت تسويته ماليًا')
        form.status = 'completed' if not form.pending else 'in_progress'
        form.closed_at = form.cancelled_at = None
    else:
        raise WorkflowError('إجراء غير معروف')
    audit(f'clearance.{status}', 'clearance', form.id)
    db.session.commit()


def send_to_finance(form, note):
    note = (note or '').strip()
    if not note:
        raise WorkflowError('اكتب الملاحظة المرسلة إلى المحاسبة')
    fin = dept_of('Finance')
    if not fin or not fin.email_list:
        raise WorkflowError('لا يوجد بريد لقسم المحاسبة — أضفه من «الأقسام»')
    form.hr_note, form.sent_to_finance_at = note[:1000], utcnow()
    audit('clearance.sent_to_finance', 'clearance', form.id)
    db.session.commit()
    return send_email(fin.email_list, f'ملاحظة من شؤون الموظفين بخصوص براءة ذمة: {form.employee_name}', NOTICE,
                      _tenant(), kind='clearance.to_finance', entity=('clearance', form.id),
                      heading='ملاحظة هامة',
                      paragraphs=[f'ملاحظة من شؤون الموظفين بخصوص الموظف «{form.employee_name}»:'],
                      note=note, code=form.ref_code,
                      items=[(r.department.name, r.note) for r in form.held] or None,
                      button_url=f'{_base()}/finance', button_label='فتح بوابة المحاسبة')


def settle(form, note, by_name):
    if form.status == 'cancelled':
        raise WorkflowError('النموذج ملغي')
    if form.settled:
        raise WorkflowError('تمت تسوية هذا النموذج مسبقًا')
    note = (note or '').strip()[:1000] or None
    form.status, form.closed_at = 'closed', utcnow()
    form.settled_at, form.settled_by, form.finance_note = utcnow(), by_name, note
    audit('clearance.settled', 'clearance', form.id)
    db.session.commit()
    hr = dept_of('HR')
    if hr and hr.email_list:
        return send_email(hr.email_list, f'تسوية مالية: براءة ذمة {form.employee_name}', NOTICE, _tenant(),
                          kind='clearance.settled', entity=('clearance', form.id), heading='تسوية مالية نهائية',
                          greeting='', paragraphs=[f'تم تسوية براءة الذمة ماليًا للموظف «{form.employee_name}».'],
                          note=note, note_tone='ok', note_label='ملاحظة المحاسبة', code=form.ref_code,
                          button_url=f'{_base()}/clearance/{form.id}', button_label='عرض النموذج')
    return None


def due_reminders(days=3):
    """Forms still waiting for signatures whose last reminder (or creation) is older than ``days``."""
    edge = utcnow() - timedelta(days=days)
    out = []
    for f in ClearanceForm.query.filter_by(status='in_progress').all():
        last = f.last_reminder_at or f.created_at
        if last < edge and f.pending:
            out.append(f)
    return out
