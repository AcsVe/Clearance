"""Tenant administration: users, roles, departments, audit and email logs,
organization settings. Everything here runs inside the logged-in user's
tenant; the tenancy layer filters every query automatically."""
import os
import re

from flask import (Blueprint, render_template, request, redirect, url_for, flash, g, abort,
                   current_app)
from sqlalchemy import func

from authz import permission_required, PERMISSIONS, permissions_local
from extensions import db
from tenancy import get_scoped
from models import (User, Role, RolePermission, UserRole, Department, UserDepartment, AuditLog,
                    EmailLog)
from models.core import utcnow, DEPARTMENT_TYPES, department_types_local
from security import hash_password, password_problems
from utils.audit import audit
from utils.helpers import split_emails, is_valid_email
from utils.i18n import pick

admin_bp = Blueprint('admin', __name__)

PAGE_SIZE = 50


def _get_or_404(model, obj_id):
    obj = get_scoped(model, obj_id)   # tenant filter applies → other tenants' ids are 404
    if obj is None:
        abort(404)
    return obj


def _ids(field):
    out = set()
    for v in request.form.getlist(field):
        try:
            out.add(int(v))
        except ValueError:
            pass
    return out


def _admins_remaining(excluding_user_id=None):
    users = User.query.filter(User.is_active.is_(True)).all()
    return [u for u in users if u.id != excluding_user_id and u.has_perm('users.manage')]


# ───────────────────────────── Users ─────────────────────────────
@admin_bp.route('/users')
@permission_required('users.manage')
def users():
    """Users as cards (grouped by role, with the tasks their roles give them) or as a table; search,
    filter and sorting run in the browser (static/js/table.js), bulk actions post to users_bulk."""
    role_counts = dict(db.session.query(UserRole.role_id, func.count(UserRole.user_id))
                       .group_by(UserRole.role_id).all())
    return render_template('admin/users.html', users=User.query.order_by(User.full_name).all(),
                           roles=Role.query.order_by(Role.id).all(), role_counts=role_counts,
                           permissions=permissions_local(), now=utcnow())


def _apply_user_form(user, is_new):
    errors = []
    f = request.form
    username = (f.get('username') or '').strip()
    full_name = (f.get('full_name') or '').strip()
    email = (f.get('email') or '').strip() or None
    if not re.fullmatch(r'[A-Za-z0-9._@-]{3,80}', username):
        errors.append(pick('اسم المستخدم: 3 أحرف على الأقل، حروف إنجليزية وأرقام و . _ - فقط', 'User name: at least 3 characters; English letters, digits and . _ - only'))
    if not full_name:
        errors.append(pick('الاسم الكامل مطلوب', 'The full name is required'))
    if email and not is_valid_email(email):
        errors.append(pick('البريد الإلكتروني غير صالح', 'The email is not valid'))
    dup = User.query.filter(func.lower(User.username) == username.lower(), User.id != (user.id or 0)).first()
    if dup:
        errors.append(pick('اسم المستخدم مستخدم مسبقًا', 'This user name is already taken'))
    if email:
        dup = User.query.filter(func.lower(User.email) == email.lower(), User.id != (user.id or 0)).first()
        if dup:
            errors.append(pick('البريد مستخدم لحساب آخر', 'The email is used by another account'))
    role_ids = _ids('roles')
    dept_ids = _ids('departments')
    valid_roles = {r.id for r in Role.query.filter(Role.id.in_(role_ids)).all()} if role_ids else set()
    valid_depts = {d.id for d in Department.query.filter(Department.id.in_(dept_ids)).all()} if dept_ids else set()
    if valid_roles != role_ids or valid_depts != dept_ids:
        errors.append(pick('اختيار غير صالح للأدوار أو الأقسام', 'Invalid choice of roles or departments'))
    temp_pw = f.get('temp_password') or ''
    if temp_pw:
        errors += password_problems(temp_pw, username)
    elif is_new and not email:
        errors.append(pick('أدخل كلمة مرور مؤقتة أو بريدًا لإرسال رابط تعيين كلمة المرور', 'Enter a temporary password, or an email to send a set-password link'))
    if errors:
        return errors, None
    user.username, user.full_name, user.email = username, full_name, email
    user.employee_number = (f.get('employee_number') or '').strip() or None
    if not is_new:
        user.is_active = f.get('is_active') == '1'
    if temp_pw:
        user.password_hash = hash_password(temp_pw)
        user.must_change_password = True
        if not is_new:
            user.session_version += 1
    elif is_new:
        user.password_hash = hash_password(os.urandom(24).hex())  # unusable until reset
    return [], (valid_roles, valid_depts, bool(temp_pw))


def _sync_links(user, role_ids, dept_ids):
    for link in UserRole.query.filter_by(user_id=user.id).all():
        if link.role_id not in role_ids:
            db.session.delete(link)
    have = {l.role_id for l in UserRole.query.filter_by(user_id=user.id).all()}
    for rid in role_ids - have:
        db.session.add(UserRole(user_id=user.id, role_id=rid))
    for link in UserDepartment.query.filter_by(user_id=user.id).all():
        if link.department_id not in dept_ids:
            db.session.delete(link)
    have = {l.department_id for l in UserDepartment.query.filter_by(user_id=user.id).all()}
    for did in dept_ids - have:
        db.session.add(UserDepartment(user_id=user.id, department_id=did))
    db.session.flush()
    db.session.expire(user, ['roles', 'departments'])


def _user_form_ctx(user, errors=None):
    return dict(u=user, errors=errors or [], roles=Role.query.order_by(Role.id).all(),
                departments=Department.query.order_by(Department.sort_order, Department.name).all())


@admin_bp.route('/users/new', methods=['GET', 'POST'])
@permission_required('users.manage')
def user_new():
    user = User(is_active=True)
    if request.method == 'POST':
        errors, extra = _apply_user_form(user, True)
        if not errors:
            role_ids, dept_ids, has_temp = extra
            db.session.add(user)
            db.session.flush()
            _sync_links(user, role_ids, dept_ids)
            audit('user.created', 'user', user.id, username=user.username,
                  roles=sorted(role_ids), departments=sorted(dept_ids))
            db.session.commit()
            if not has_temp:
                from blueprints.auth import _issue_reset
                res = _issue_reset(user, g.tenant, by_admin=True)
                flash(pick('تم إنشاء المستخدم وإرسال رابط تعيين كلمة المرور', 'The user was created and a set-password link was sent') if res else
                      pick('تم إنشاء المستخدم، لكن تعذّر إرسال البريد — راجع سجل البريد', 'The user was created, but the email could not be sent — see the mail log'),
                      'success' if res else 'warning')
            else:
                flash(pick('تم إنشاء المستخدم؛ سيُطلب منه تغيير كلمة المرور عند أول دخول', 'The user was created; they must change the password at the first sign-in'), 'success')
            return redirect(url_for('admin.users'))
        return render_template('admin/user_form.html', **_user_form_ctx(user, errors)), 400
    return render_template('admin/user_form.html', **_user_form_ctx(user))


@admin_bp.route('/users/<int:user_id>', methods=['GET', 'POST'])
@permission_required('users.manage')
def user_edit(user_id):
    user = _get_or_404(User, user_id)
    if request.method == 'POST':
        errors, extra = _apply_user_form(user, False)
        if not errors:
            role_ids, dept_ids, _ = extra
            _sync_links(user, role_ids, dept_ids)
            if not _admins_remaining() :
                db.session.rollback()
                errors = [pick('لا يمكن: يجب أن يبقى مستخدم فعّال واحد على الأقل بصلاحية إدارة المستخدمين', 'Not possible: at least one active user must keep the user-management permission')]
            else:
                if not user.is_active:
                    user.session_version += 1
                audit('user.updated', 'user', user.id, roles=sorted(role_ids),
                      departments=sorted(dept_ids), active=user.is_active)
                db.session.commit()
                flash(pick('تم الحفظ', 'Saved'), 'success')
                return redirect(url_for('admin.users'))
        db.session.rollback()
        user = _get_or_404(User, user_id)
        return render_template('admin/user_form.html', **_user_form_ctx(user, errors)), 400
    return render_template('admin/user_form.html', **_user_form_ctx(user))


@admin_bp.route('/users/<int:user_id>/action', methods=['POST'])
@permission_required('users.manage')
def user_action(user_id):
    user = _get_or_404(User, user_id)
    action = request.form.get('action')
    if action == 'unlock':
        user.locked_until, user.failed_logins = None, 0
        audit('user.unlocked', 'user', user.id)
        db.session.commit()
        flash(pick('تم فك القفل', 'Unlocked'), 'success')
    elif action == 'logout_all':
        user.session_version += 1
        audit('user.sessions_revoked', 'user', user.id)
        db.session.commit()
        flash(pick('تم إنهاء جميع جلسات المستخدم', 'All the user sessions were ended'), 'success')
    elif action == 'send_reset':
        if not user.email:
            flash(pick('لا يوجد بريد لهذا المستخدم', 'This user has no email'), 'danger')
        else:
            from blueprints.auth import _issue_reset
            res = _issue_reset(user, g.tenant, by_admin=True)
            flash(pick('تم إرسال رابط إعادة التعيين', 'The reset link was sent') if res else pick('تعذّر الإرسال — راجع سجل البريد', 'Sending failed — see the mail log'),
                  'success' if res else 'danger')
    else:
        abort(400)
    return redirect(url_for('admin.user_edit', user_id=user.id))


def _bulk_done(n, skipped, verb):
    msg = f'{verb}: {n}'
    if skipped:
        msg += pick(' — تُرك: ', ' — skipped: ') + pick('، ', ', ').join(skipped)
    flash(msg, 'success' if n else 'warning')


@admin_bp.route('/users/bulk', methods=['POST'])
@permission_required('users.manage')
def users_bulk():
    """Bulk actions on the ticked users: activate / deactivate / delete / logout_all.
    The signed-in user and the last remaining administrator are never deactivated or deleted."""
    action = request.form.get('action')
    if action not in ('activate', 'deactivate', 'delete', 'logout_all'):
        abort(400)
    me = g.user.id
    picked = User.query.filter(User.id.in_(_ids('ids'))).all()
    done, skipped = 0, []
    for u in picked:
        if action in ('deactivate', 'delete'):
            if u.id == me:
                skipped.append(pick(f'{u.full_name} (حسابك)', f'{u.full_name} (your account)'))
                continue
            # earlier rows of this batch are already applied (autoflush), so this sees the batch so far
            if u.has_perm('users.manage') and not _admins_remaining(excluding_user_id=u.id):
                skipped.append(pick(f'{u.full_name} (آخر مدير)', f'{u.full_name} (the last administrator)'))
                continue
        if action == 'activate':
            u.is_active = True
        elif action == 'deactivate':
            u.is_active = False
            u.session_version += 1
        elif action == 'logout_all':
            u.session_version += 1
        else:
            audit('user.deleted', 'user', u.id, username=u.username, name=u.full_name)
            db.session.delete(u)
            done += 1
            continue
        audit(f'user.bulk_{action}', 'user', u.id)
        done += 1
    db.session.commit()
    _bulk_done(done, skipped, {'activate': pick('فُعِّل', 'Activated'), 'deactivate': pick('أُوقف', 'Deactivated'), 'delete': pick('حُذف', 'Deleted'),
                               'logout_all': pick('أُنهيت جلسات', 'Sessions ended')}[action])
    return redirect(request.referrer or url_for('admin.users'))


# ─────────────────────────── Departments ───────────────────────────
@admin_bp.route('/departments')
@permission_required('departments.manage')
def departments():
    depts = Department.query.order_by(Department.sort_order, Department.name).all()
    return render_template('admin/departments.html', departments=depts, types=dict(department_types_local()))


def _apply_dept_form(dept):
    f = request.form
    errors = []
    code = (f.get('code') or '').strip()
    name = (f.get('name') or '').strip()
    dtype = f.get('dept_type') or 'normal'
    raw_emails = f.get('emails') or ''
    emails = split_emails(raw_emails)
    if not code or len(code) > 50:
        errors.append(pick('الكود مطلوب (حتى 50 حرفًا)', 'The code is required (up to 50 characters)'))
    if not name:
        errors.append(pick('اسم القسم مطلوب', 'The department name is required'))
    if dtype not in dict(DEPARTMENT_TYPES):
        errors.append(pick('نوع القسم غير صالح', 'Invalid department type'))
    bad = [p for p in re.split(r'[;,\s]+', raw_emails) if p.strip() and p.strip() not in emails
           and p.strip().lower() not in {e.lower() for e in emails}]
    if bad:
        errors.append(pick('بريد غير صالح: ', 'Invalid email: ') + ', '.join(bad))
    if Department.query.filter(Department.code == code, Department.id != (dept.id or 0)).first():
        errors.append(pick('الكود مستخدم لقسم آخر', 'The code is used by another department'))
    active = f.get('is_active', '1') == '1'
    if dtype in ('HR', 'Finance') and active:
        other = Department.query.filter(Department.dept_type == dtype, Department.is_active.is_(True),
                                        Department.id != (dept.id or 0)).first()
        if other:
            errors.append(pick(f'يوجد قسم فعّال آخر من النوع نفسه: {other.name}', f'Another active department has the same type: {other.name}'))
    if errors:
        return errors
    dept.code, dept.name, dept.dept_type = code, name, dtype
    dept.icon = (f.get('icon') or '🏢').strip()[:20]
    dept.manager_name = (f.get('manager_name') or '').strip() or None
    dept.emails = ', '.join(emails) or None
    try:
        dept.sort_order = int(f.get('sort_order') or 0)
    except ValueError:
        dept.sort_order = 0
    dept.is_active = active
    return []


@admin_bp.route('/departments/new', methods=['GET', 'POST'])
@admin_bp.route('/departments/<int:dept_id>', methods=['GET', 'POST'])
@permission_required('departments.manage')
def department_form(dept_id=None):
    dept = _get_or_404(Department, dept_id) if dept_id else Department(is_active=True, dept_type='normal')
    errors = []
    if request.method == 'POST':
        errors = _apply_dept_form(dept)
        if not errors:
            if not dept_id:
                db.session.add(dept)
            db.session.flush()
            audit('department.created' if not dept_id else 'department.updated', 'department',
                  dept.id, code=dept.code, name=dept.name, type=dept.dept_type)
            db.session.commit()
            flash(pick('تم الحفظ', 'Saved'), 'success')
            return redirect(url_for('admin.departments'))
        db.session.rollback()
    return render_template('admin/department_form.html', d=dept, errors=errors,
                           types=department_types_local()), (400 if errors else 200)


def department_in_use(dept):
    """Why a department cannot be deleted (records that depend on it), or '' when it is free."""
    reasons = []
    try:
        from models import ClearanceResponse, Custody, Employee
        if ClearanceResponse.query.filter_by(department_id=dept.id).first():
            reasons.append(pick('له ردود على نماذج براءة ذمة', 'it has replies on clearance forms'))
        if Custody.query.filter_by(department_id=dept.id).first():
            reasons.append(pick('له عهد مسجّلة', 'it has custody records'))
        if Employee.query.filter_by(department_id=dept.id).first():
            reasons.append(pick('مرتبط بموظفين', 'it has employees'))
    except ImportError:
        pass
    return pick('، ', ', ').join(reasons)


@admin_bp.route('/departments/bulk', methods=['POST'])
@permission_required('departments.manage')
def departments_bulk():
    action = request.form.get('action')
    if action not in ('activate', 'deactivate', 'delete'):
        abort(400)
    done, skipped = 0, []
    for d in Department.query.filter(Department.id.in_(_ids('ids'))).all():
        if action == 'activate':
            if d.dept_type in ('HR', 'Finance') and Department.query.filter(
                    Department.dept_type == d.dept_type, Department.is_active.is_(True),
                    Department.id != d.id).first():
                skipped.append(pick(f'{d.name} (يوجد قسم فعّال من النوع نفسه)', f'{d.name} (an active department of the same type exists)'))
                continue
            d.is_active = True
        elif action == 'deactivate':
            d.is_active = False
        else:
            why = department_in_use(d)
            if why:
                skipped.append(pick(f'{d.name} ({why} — أوقفه بدلاً من حذفه)', f'{d.name} ({why} — deactivate it instead)'))
                continue
            audit('department.deleted', 'department', d.id, code=d.code, name=d.name)
            db.session.delete(d)
            done += 1
            continue
        audit(f'department.bulk_{action}', 'department', d.id)
        done += 1
    db.session.commit()
    _bulk_done(done, skipped, {'activate': pick('فُعِّل', 'Activated'), 'deactivate': pick('أُوقف', 'Deactivated'), 'delete': pick('حُذف', 'Deleted')}[action])
    return redirect(url_for('admin.departments'))


# ───────────────────────────── Roles ─────────────────────────────
@admin_bp.route('/roles')
@permission_required('users.manage')
def roles():
    role_counts = dict(db.session.query(UserRole.role_id, func.count(UserRole.user_id))
                       .group_by(UserRole.role_id).all())
    return render_template('admin/roles.html', roles=Role.query.order_by(Role.id).all(),
                           permissions=permissions_local(), role_counts=role_counts)


@admin_bp.route('/roles/new', methods=['GET', 'POST'])
@admin_bp.route('/roles/<int:role_id>', methods=['GET', 'POST'])
@permission_required('users.manage')
def role_form(role_id=None):
    role = _get_or_404(Role, role_id) if role_id else Role(is_active=True)
    errors = []
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        codes = {c for c in request.form.getlist('perms') if c in PERMISSIONS}
        if not name:
            errors.append(pick('اسم الدور مطلوب', 'The role name is required'))
        if not errors:
            role.name = name
            role.description = (request.form.get('description') or '').strip() or None
            if not role_id:
                role.key = 'custom_' + os.urandom(4).hex()
                db.session.add(role)
                db.session.flush()
            current = {p.permission_code: p for p in role.permissions}
            for code, rp in current.items():
                if code not in codes:
                    role.permissions.remove(rp)
            for code in codes - set(current):
                role.permissions.append(RolePermission(permission_code=code))
            db.session.flush()
            db.session.expire_all()
            if not _admins_remaining():
                db.session.rollback()
                errors.append(pick('لا يمكن إزالة صلاحية إدارة المستخدمين من آخر مدير', 'The last administrator cannot lose the user-management permission'))
            else:
                audit('role.saved', 'role', role.id, name=name, permissions=sorted(codes))
                db.session.commit()
                flash(pick('تم الحفظ', 'Saved'), 'success')
                return redirect(url_for('admin.roles'))
        if role_id:
            role = _get_or_404(Role, role_id)
    return render_template('admin/role_form.html', role=role, errors=errors,
                           permissions=permissions_local()), (400 if errors else 200)


@admin_bp.route('/roles/bulk', methods=['POST'])
@permission_required('users.manage')
def roles_bulk():
    if request.form.get('action') != 'delete':
        abort(400)
    done, skipped = 0, []
    for r in Role.query.filter(Role.id.in_(_ids('ids'))).all():
        if r.is_system:
            skipped.append(pick(f'{r.name} (دور أساسي)', f'{r.name} (built-in role)'))
            continue
        if UserRole.query.filter_by(role_id=r.id).first():
            skipped.append(pick(f'{r.name} (مسند إلى مستخدمين)', f'{r.name} (given to users)'))
            continue
        audit('role.deleted', 'role', r.id, name=r.name)
        db.session.delete(r)
        done += 1
    db.session.commit()
    _bulk_done(done, skipped, pick('حُذف', 'Deleted'))
    return redirect(url_for('admin.roles'))


# ─────────────────────────── Logs ───────────────────────────
@admin_bp.route('/audit')
@permission_required('audit.view')
def audit_log():
    page = max(int(request.args.get('page', 1) or 1), 1)
    action = (request.args.get('action') or '').strip()
    q = AuditLog.query
    if action:
        q = q.filter(AuditLog.action.like(f'{action}%'))
    rows = q.order_by(AuditLog.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1).all()
    from utils.audit_labels import groups_local
    return render_template('admin/audit.html', rows=rows[:PAGE_SIZE], page=page,
                           has_next=len(rows) > PAGE_SIZE, action=action, groups=groups_local())


@admin_bp.route('/email-log')
@permission_required('email_log.view')
def email_log():
    only_failed = request.args.get('failed') == '1'
    q = EmailLog.query
    if only_failed:
        q = q.filter(EmailLog.status != 'sent')
    rows = q.order_by(EmailLog.id.desc()).limit(200).all()
    return render_template('admin/email_log.html', rows=rows, only_failed=only_failed)


@admin_bp.route('/email-log/ack', methods=['POST'])
@permission_required('email_log.view')
def email_log_ack():
    n = EmailLog.query.filter(EmailLog.status != 'sent', EmailLog.acknowledged.is_(False)) \
        .update({'acknowledged': True}, synchronize_session=False)
    audit('email_log.acknowledged', count=n)
    db.session.commit()
    return redirect(url_for('admin.email_log', failed=1))


@admin_bp.route('/email-log/bulk', methods=['POST'])
@permission_required('email_log.view')
def email_log_bulk():
    action = request.form.get('action')
    q = EmailLog.query.filter(EmailLog.id.in_(_ids('ids')))
    if action == 'ack':
        n = q.update({'acknowledged': True}, synchronize_session=False)
        verb = pick('عُلِّم كمقروء', 'Marked as read')
    elif action == 'delete':
        n = 0
        for row in q.all():
            db.session.delete(row)
            n += 1
        verb = pick('حُذف', 'Deleted')
    else:
        abort(400)
    audit(f'email_log.bulk_{action}', count=n)
    db.session.commit()
    _bulk_done(n, [], verb)
    return redirect(request.referrer or url_for('admin.email_log'))


# ─────────────────────────── Settings ───────────────────────────
_IMAGE_MAGIC = {b'\x89PNG': '.png', b'\xff\xd8\xff': '.jpg', b'RIFF': '.webp'}


def _save_logo(file_storage, which):
    head = file_storage.stream.read(12)
    file_storage.stream.seek(0)
    ext = next((e for magic, e in _IMAGE_MAGIC.items() if head.startswith(magic)), None)
    if ext == '.webp' and head[8:12] != b'WEBP':
        ext = None
    if not ext:
        raise ValueError(pick('الشعار يجب أن يكون صورة PNG أو JPG أو WEBP', 'The logo must be a PNG, JPG or WEBP image'))
    folder = os.path.join(current_app.config['UPLOAD_DIR'], 'tenants', str(g.tenant.id))
    os.makedirs(folder, exist_ok=True)
    name = f'logo_{which}_{os.urandom(4).hex()}{ext}'
    file_storage.save(os.path.join(folder, name))
    return name


@admin_bp.route('/settings', methods=['GET', 'POST'])
@permission_required('settings.manage')
def settings():
    from models import Tenant
    t = db.session.get(Tenant, g.tenant.id)
    errors = []
    if request.method == 'POST':
        f = request.form
        name_ar = (f.get('name_ar') or '').strip()
        accent = (f.get('accent_color') or '').strip()
        sender = (f.get('mail_sender') or '').strip()
        if not name_ar:
            errors.append(pick('الاسم العربي مطلوب', 'The Arabic name is required'))
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', accent):
            errors.append(pick('اختر اللون من أداة الألوان', 'Choose the colour with the colour picker'))
        if sender and not is_valid_email(sender):
            errors.append(pick('بريد المرسل غير صالح', 'The sender mailbox is not valid'))
        logos = {}
        for which in ('primary', 'secondary'):
            fs = request.files.get(f'logo_{which}')
            if fs and fs.filename:
                try:
                    logos[which] = _save_logo(fs, which)
                except ValueError as e:
                    errors.append(str(e))
        if not errors:
            t.name_ar, t.accent_color = name_ar, accent
            t.name_en = (f.get('name_en') or '').strip() or None
            t.mail_sender = sender or None
            t.email_footer = (f.get('email_footer') or '').strip() or None
            t.form_footer = (f.get('form_footer') or '').strip() or None
            removed = []
            for which in ('primary', 'secondary'):
                if which in logos:
                    setattr(t, f'logo_{which}', logos[which])
                elif f.get(f'remove_logo_{which}') and getattr(t, f'logo_{which}'):
                    setattr(t, f'logo_{which}', None)   # back to the system icon
                    removed.append(which)
            audit('settings.updated', 'tenant', t.id, logos=sorted(logos), removed=removed)
            db.session.commit()
            flash(pick('تم حفظ الإعدادات', 'Settings saved'), 'success')
            return redirect(url_for('admin.settings'))
    cfg = current_app.config
    graph = {'ready': all(cfg.get(k) for k in ('MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET')),
             'tenant': bool(cfg.get('MS_TENANT_ID')), 'client': bool(cfg.get('MS_CLIENT_ID')),
             'secret': bool(cfg.get('MS_CLIENT_SECRET')),
             'sender': t.mail_sender or cfg.get('MS_SENDER_EMAIL') or ''}
    return render_template('admin/settings.html', t=t, errors=errors, graph=graph), (400 if errors else 200)


@admin_bp.route('/settings/import-sheet', methods=['POST'])
@permission_required('settings.manage')
def import_sheet():
    fs = request.files.get('file')
    if not fs or not fs.filename.lower().endswith('.xlsx'):
        flash(pick('اختر ملف Excel ‏(.xlsx) المحمَّل من Google Sheets', 'Choose the Excel (.xlsx) file downloaded from Google Sheets'), 'danger')
        return redirect(url_for('admin.settings'))
    from services.sheets_import import import_workbook
    try:
        rep = import_workbook(fs.read())
    except Exception as exc:  # noqa: BLE001 — a damaged or unrelated file
        db.session.rollback()
        current_app.logger.exception('sheet import failed')
        flash(pick('تعذّر الاستيراد: تأكد أن الملف هو ملف Google Sheet القديم بصيغة Excel', 'The import failed: make sure the file is the old Google Sheet saved as Excel'), 'danger')
        return redirect(url_for('admin.settings'))
    audit('sheet.imported', **{k: v for k, v in rep.items() if k != 'problems'})
    db.session.commit()
    flash(pick(f'أقسام: {rep["departments"]}، نماذج: {rep["forms"]}، ردود: {rep["responses"]}، موظفون: {rep["employees"]}، '
               f'عهد: {rep["custody"]}، تُرك (موجود مسبقًا): {rep["skipped"]}',
               f'Departments: {rep["departments"]}, forms: {rep["forms"]}, replies: {rep["responses"]}, employees: {rep["employees"]}, '
               f'custody: {rep["custody"]}, skipped (already there): {rep["skipped"]}'), 'success')
    for p in rep['problems'][:15]:
        flash(p, 'warning')
    return redirect(url_for('admin.settings'))
