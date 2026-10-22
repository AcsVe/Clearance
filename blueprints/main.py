from flask import Blueprint, render_template, g, redirect, url_for
from sqlalchemy import func

from authz import login_required, current_user
from extensions import db

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if g.get('superadmin'):
        return redirect(url_for('super.tenants'))
    if not g.get('user'):
        return redirect(url_for('auth.login'))
    return redirect(url_for('main.home'))


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
        out.append(('براءة الذمة', [
            _kpi('قيد التنفيذ', sum(f.status == 'in_progress' for f in forms), L('الحالة:قيد التنفيذ'),
                 'بانتظار توقيع الأقسام', 'warn'),
            _kpi('مكتملة — فيها تعليق', sum(f.status == 'completed' and bool(f.held) for f in forms),
                 L('معلق لدى:معلق'), 'تحتاج متابعة أو تسوية', 'bad'),
            _kpi('مكتملة — خالية', sum(f.status == 'completed' and not f.held for f in forms), L('الحالة:مكتمل'),
                 'جاهزة للطباعة والإقفال', 'ok'),
            _kpi('مقفلة', sum(f.status == 'closed' for f in forms), L('الحالة:مقفل'), ''),
            _kpi('تمت تسويتها', sum(f.settled for f in forms), L('الحالة:تمت التسوية'), '', 'ok'),
            _kpi('ملغاة', sum(f.status == 'cancelled' for f in forms), L('الحالة:ملغي'), ''),
        ]))
    if has_perm('clearance.sign') and user.department_ids:
        n = (ClearanceResponse.query.join(ClearanceForm)
             .filter(ClearanceResponse.department_id.in_(list(user.department_ids)),
                     ClearanceResponse.status == 'pending',
                     ClearanceForm.status.in_(('in_progress', 'completed'))).count())
        out.append(('قسمي', [_kpi('بانتظار توقيعي', n, url_for('clearance.portal'), 'اضغط للتوقيع', 'warn')]))
    if has_perm('finance.settle'):
        pend = [f for f in ClearanceForm.query.filter(ClearanceForm.status.in_(('in_progress', 'completed'))).all()
                if f.held or f.sent_to_finance_at]
        out.append(('المحاسبة', [_kpi('بانتظار التسوية', len(pend), url_for('clearance.finance'), '', 'bad')]))
    if has_perm('custody.view_all') or has_perm('custody.manage_all') or has_perm('custody.manage'):
        from blueprints.custody import visible_query
        rows = visible_query(user).all()
        leaving = {f.employee_id for f in ClearanceForm.query.filter(
            ClearanceForm.status.in_(('in_progress', 'completed')), ClearanceForm.employee_id.isnot(None)).all()}
        L = lambda f: url_for('custody.index', f=f)  # noqa: E731
        out.append(('العهد', [
            _kpi('عهد مسلّمة', sum(c.status == 'handed' for c in rows), L('الحالة:مسلمة'), '', 'warn'),
            _kpi('على موظفين في إجراءات براءة الذمة', sum(c.status == 'handed' and c.employee_id in leaving for c in rows),
                 L('الحالة:مسلمة'), 'يجب استرجاعها قبل التوقيع', 'bad'),
            _kpi('مسترجعة', sum(c.status == 'returned' for c in rows), L('الحالة:مسترجعة'), '', 'ok'),
        ]))
    if has_perm('employees.manage'):
        emps = Employee.query.all()
        L = lambda f: url_for('employees.index', f=f)  # noqa: E731
        out.append(('الموظفون', [
            _kpi('موظفون فعّالون', sum(e.is_active for e in emps), L('الحالة:فعّال'), '', 'ok'),
            _kpi('بلا قسم', sum(e.department_id is None for e in emps), L('القسم:—'), '', 'warn'),
            _kpi('بلا بريد', sum(not e.email for e in emps), L('البريد:—'), 'لن تصلهم الإشعارات', 'warn'),
            _kpi('بلا حساب دخول', sum(e.user_id is None for e in emps), L('الحساب:بلا حساب'),
                 'أنشئ حساباتهم من القائمة', ''),
        ]))
    if has_perm('users.manage'):
        users = User.query.all()
        now = utcnow()
        with_role = {uid for (uid,) in db.session.query(UserRole.user_id).distinct()}
        out.append(('المستخدمون', [
            _kpi('الحسابات الفعّالة', sum(u.is_active for u in users), url_for('admin.users', f='الحالة:فعّال'), 'اضغط للقائمة', 'ok'),
            _kpi('الحسابات الموقوفة', sum(not u.is_active for u in users), url_for('admin.users', f='الحالة:موقوف'), '', 'bad'),
            _kpi('مقفلة مؤقتًا', sum(bool(u.is_active and u.locked_until and u.locked_until > now) for u in users),
                 url_for('admin.users', f='الحالة:مقفل مؤقتًا'), 'محاولات دخول خاطئة', 'warn'),
            _kpi('بلا دور', sum(u.id not in with_role for u in users), url_for('admin.users'), 'لا يستطيعون أداء أي مهمة', 'warn'),
            _kpi('لم يدخلوا بعد', sum(u.last_login is None for u in users), url_for('admin.users'), 'أرسل لهم رابط كلمة المرور'),
        ]))
    if has_perm('departments.manage'):
        depts = Department.query.all()
        active = [d for d in depts if d.is_active]
        k = [
            _kpi('أقسام فعّالة', len(active), url_for('admin.departments', f='الحالة:فعّال'), '', 'ok'),
            _kpi('أقسام متوقفة', len(depts) - len(active), url_for('admin.departments', f='الحالة:متوقف'), ''),
            _kpi('أقسام بلا بريد', sum(not d.emails for d in active), url_for('admin.departments', f='البريد:—'),
                 'لن تصلها روابط التوقيع', 'bad'),
        ]
        for t, name in (('HR', 'شؤون الموظفين'), ('Finance', 'المحاسبة')):
            if not any(d.dept_type == t for d in active):
                k.append(_kpi(f'لا يوجد قسم {name}', 1, url_for('admin.department_form'), 'أضفه ليكتمل سير العمل', 'bad'))
        out.append(('الأقسام', k))
    if has_perm('email_log.view'):
        failed = EmailLog.query.filter(EmailLog.status != 'sent', EmailLog.acknowledged.is_(False)).count()
        sent = EmailLog.query.filter(EmailLog.status == 'sent').count()
        out.append(('البريد', [
            _kpi('أخطاء بريد جديدة', failed, url_for('admin.email_log', failed=1), 'رسائل لم تصل', 'bad'),
            _kpi('رسائل أُرسلت', sent, url_for('admin.email_log', f='الحالة:أُرسل'), '', 'ok'),
        ]))
    return out


@main_bp.route('/home')
@login_required
def home():
    return render_template('home.html', sections=dashboard_sections())
