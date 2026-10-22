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
    out = []
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
