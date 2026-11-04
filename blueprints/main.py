from flask import Blueprint, render_template, g, redirect, url_for
from sqlalchemy import func

from authz import login_required, current_user
from extensions import db
from utils.i18n import pick

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if g.get('superadmin'):
        return redirect(url_for('super.tenants'))
    if not g.get('user'):
        return redirect(url_for('auth.login'))
    return redirect(url_for('main.home'))


def F(col_ar, col_en, val_ar, val_en):
    """The ?f=<column>:<value> filter of a list, in the interface language (it must match the column title and cell text)."""
    return pick(f'{col_ar}:{val_ar}', f'{col_en}:{val_en}')


def _kpi(label, n, url, hint='', tone=''):
    return {'label': label, 'n': n, 'url': url, 'hint': hint, 'tone': tone if n else 'zero'}


def has_perm(code):
    u = current_user()
    return bool(u and u.has_perm(code))


def dashboard_sections():
    """[(section title, [kpi…])] for what the signed-in user may see. Every number links to the list
    behind it, already filtered (?f=<column>:<value>)."""
    from models import User, Department, EmailLog, UserRole
    from models.core import utcnow
    from models import ClearanceForm, ClearanceResponse, Custody, Employee
    out = []
    user = current_user()
    if has_perm('clearance.view_all') or has_perm('clearance.manage') or has_perm('clearance.create'):
        forms = ClearanceForm.query.all()
        L = lambda f: url_for('clearance.index', f=f)  # noqa: E731
        out.append((pick('براءة الذمة', 'Clearance'), [
            _kpi(pick('قيد التنفيذ', 'In progress'), sum(f.status == 'in_progress' for f in forms),
                 L(F('الحالة', 'Status', 'قيد التنفيذ', 'In progress')), pick('بانتظار توقيع الأقسام', 'Awaiting the departments'), 'warn'),
            _kpi(pick('مكتملة — فيها تعليق', 'Completed — with a hold'), sum(f.status == 'completed' and bool(f.held) for f in forms),
                 L(F('معلق لدى', 'On hold at', 'معلق', 'On hold')), pick('تحتاج متابعة أو تسوية', 'Need follow-up or settlement'), 'bad'),
            _kpi(pick('مكتملة — خالية', 'Completed — cleared'), sum(f.status == 'completed' and not f.held for f in forms),
                 L(F('الحالة', 'Status', 'مكتمل', 'Completed')), pick('جاهزة للطباعة والإقفال', 'Ready to print and close'), 'ok'),
            _kpi(pick('مقفلة', 'Closed'), sum(f.status == 'closed' for f in forms), L(F('الحالة', 'Status', 'مقفل', 'Closed')), ''),
            _kpi(pick('تمت تسويتها', 'Settled'), sum(f.settled for f in forms), L(F('الحالة', 'Status', 'تمت التسوية', 'Settled')), '', 'ok'),
            _kpi(pick('ملغاة', 'Cancelled'), sum(f.status == 'cancelled' for f in forms), L(F('الحالة', 'Status', 'ملغي', 'Cancelled')), ''),
        ]))
    if has_perm('clearance.sign') and user.department_ids:
        n = (ClearanceResponse.query.join(ClearanceForm)
             .filter(ClearanceResponse.department_id.in_(list(user.department_ids)),
                     ClearanceResponse.status == 'pending',
                     ClearanceForm.status.in_(('in_progress', 'completed'))).count())
        out.append((pick('قسمي', 'My department'), [_kpi(pick('بانتظار توقيعي', 'Awaiting my signature'), n, url_for('clearance.portal'),
                                                         pick('اضغط للتوقيع', 'Press to sign'), 'warn')]))
    if has_perm('finance.settle'):
        pend = [f for f in ClearanceForm.query.filter(ClearanceForm.status.in_(('in_progress', 'completed'))).all()
                if f.held or f.sent_to_finance_at]
        out.append((pick('المحاسبة', 'Finance'), [_kpi(pick('بانتظار التسوية', 'Awaiting settlement'), len(pend), url_for('clearance.finance'), '', 'bad')]))
    if has_perm('custody.view_all') or has_perm('custody.manage_all') or has_perm('custody.manage'):
        from blueprints.custody import visible_query
        rows = visible_query(user).all()
        leaving = {f.employee_id for f in ClearanceForm.query.filter(
            ClearanceForm.status.in_(('in_progress', 'completed')), ClearanceForm.employee_id.isnot(None)).all()}
        L = lambda f: url_for('custody.index', f=f)  # noqa: E731
        handed = F('الحالة', 'Status', 'مسلمة', 'Handed over')
        out.append((pick('العهد', 'Custody'), [
            _kpi(pick('عهد مسلّمة', 'Items handed over'), sum(c.status == 'handed' for c in rows), L(handed), '', 'warn'),
            _kpi(pick('على موظفين في إجراءات براءة الذمة', 'Held by employees in clearance'),
                 sum(c.status == 'handed' and c.employee_id in leaving for c in rows),
                 L(handed), pick('يجب استرجاعها قبل التوقيع', 'Must be returned before signing'), 'bad'),
            _kpi(pick('مسترجعة', 'Returned'), sum(c.status == 'returned' for c in rows), L(F('الحالة', 'Status', 'مسترجعة', 'Returned')), '', 'ok'),
        ]))
    if has_perm('employees.manage'):
        emps = Employee.query.all()
        L = lambda f: url_for('employees.index', f=f)  # noqa: E731
        out.append((pick('الموظفون', 'Employees'), [
            _kpi(pick('موظفون فعّالون', 'Active employees'), sum(e.is_active for e in emps), L(F('الحالة', 'Status', 'فعّال', 'Active')), '', 'ok'),
            _kpi(pick('بلا قسم', 'No department'), sum(e.department_id is None for e in emps), L(F('القسم', 'Department', '—', '—')), '', 'warn'),
            _kpi(pick('بلا بريد', 'No email'), sum(not e.email for e in emps), L(F('البريد', 'Email', '—', '—')),
                 pick('لن تصلهم الإشعارات', 'They will get no notifications'), 'warn'),
            _kpi(pick('بلا حساب دخول', 'No sign-in account'), sum(e.user_id is None for e in emps), L(F('الحساب', 'Account', 'بلا حساب', 'No account')),
                 pick('أنشئ حساباتهم من القائمة', 'Create their accounts from the list'), ''),
        ]))
    if has_perm('users.manage'):
        users = User.query.all()
        now = utcnow()
        with_role = {uid for (uid,) in db.session.query(UserRole.user_id).distinct()}
        U = lambda f: url_for('admin.users', f=f)  # noqa: E731
        out.append((pick('المستخدمون', 'Users'), [
            _kpi(pick('الحسابات الفعّالة', 'Active accounts'), sum(u.is_active for u in users), U(F('الحالة', 'Status', 'فعّال', 'Active')),
                 pick('اضغط للقائمة', 'Press for the list'), 'ok'),
            _kpi(pick('الحسابات الموقوفة', 'Inactive accounts'), sum(not u.is_active for u in users), U(F('الحالة', 'Status', 'موقوف', 'Inactive')), '', 'bad'),
            _kpi(pick('مقفلة مؤقتًا', 'Temporarily locked'), sum(bool(u.is_active and u.locked_until and u.locked_until > now) for u in users),
                 U(F('الحالة', 'Status', 'مقفل مؤقتًا', 'Temporarily locked')), pick('محاولات دخول خاطئة', 'Wrong sign-in attempts'), 'warn'),
            _kpi(pick('بلا دور', 'No role'), sum(u.id not in with_role for u in users), url_for('admin.users'),
                 pick('لا يستطيعون أداء أي مهمة', 'They cannot do any task'), 'warn'),
            _kpi(pick('لم يدخلوا بعد', 'Never signed in'), sum(u.last_login is None for u in users), url_for('admin.users'),
                 pick('أرسل لهم رابط كلمة المرور', 'Send them the password link')),
        ]))
    if has_perm('departments.manage'):
        depts = Department.query.all()
        active = [d for d in depts if d.is_active]
        D = lambda f: url_for('admin.departments', f=f)  # noqa: E731
        k = [
            _kpi(pick('أقسام فعّالة', 'Active departments'), len(active), D(F('الحالة', 'Status', 'فعّال', 'Active')), '', 'ok'),
            _kpi(pick('أقسام متوقفة', 'Inactive departments'), len(depts) - len(active), D(F('الحالة', 'Status', 'متوقف', 'Inactive')), ''),
            _kpi(pick('أقسام بلا بريد', 'Departments without email'), sum(not d.emails for d in active), D(F('البريد', 'Email', '—', '—')),
                 pick('لن تصلها روابط التوقيع', 'They will get no signing links'), 'bad'),
        ]
        for t, name, name_en in (('HR', 'شؤون الموظفين', 'Human resources'), ('Finance', 'المحاسبة', 'Finance')):
            if not any(d.dept_type == t for d in active):
                k.append(_kpi(pick(f'لا يوجد قسم {name}', f'There is no {name_en} department'), 1, url_for('admin.department_form'),
                              pick('أضفه ليكتمل سير العمل', 'Add it to complete the workflow'), 'bad'))
        out.append((pick('الأقسام', 'Departments'), k))
    if has_perm('email_log.view'):
        failed = EmailLog.query.filter(EmailLog.status != 'sent', EmailLog.acknowledged.is_(False)).count()
        sent = EmailLog.query.filter(EmailLog.status == 'sent').count()
        out.append((pick('البريد', 'Mail'), [
            _kpi(pick('أخطاء بريد جديدة', 'New mail errors'), failed, url_for('admin.email_log', failed=1),
                 pick('رسائل لم تصل', 'Messages that did not arrive'), 'bad'),
            _kpi(pick('رسائل أُرسلت', 'Messages sent'), sent, url_for('admin.email_log', f=F('الحالة', 'Status', 'أُرسل', 'Sent')), '', 'ok'),
        ]))
    return out


@main_bp.route('/home')
@login_required
def home():
    return render_template('home.html', sections=dashboard_sections())
