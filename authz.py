"""Permissions catalog, default roles and access decorators."""
from functools import wraps

from flask import g, abort, redirect, url_for, request, session

# code -> (Arabic label, group)
PERMISSIONS = {
    'clearance.create':     ('إنشاء نموذج براءة ذمة', 'براءة الذمة'),
    'clearance.view_all':   ('عرض كل نماذج براءة الذمة', 'براءة الذمة'),
    'clearance.manage':     ('إلغاء/إقفال/تذكير/إرسال للمحاسبة', 'براءة الذمة'),
    'clearance.sign':       ('توقيع براءة الذمة لقسمه فقط', 'براءة الذمة'),
    'finance.settle':       ('التسوية المالية', 'المحاسبة'),
    'custody.manage':       ('تسليم واسترجاع عهد قسمه فقط', 'العهد'),
    'custody.view_all':     ('عرض عهد كل الأقسام', 'العهد'),
    'custody.manage_all':   ('تسليم واسترجاع عهد أي قسم', 'العهد'),
    'custody.import':       ('استيراد العهد الورقية (OCR) لقسمه', 'العهد'),
    'reports.view':         ('رؤية التقارير', 'التقارير'),
    'employees.manage':     ('إدارة سجل الموظفين (إضافة واستيراد وتعديل)', 'الموظفون'),
    'users.manage':         ('إدارة المستخدمين والأدوار', 'الإدارة'),
    'departments.manage':   ('إدارة الأقسام', 'الإدارة'),
    'settings.manage':      ('إعدادات المنظمة', 'الإدارة'),
    'audit.view':           ('سجل التدقيق', 'الإدارة'),
    'email_log.view':       ('سجل البريد وأخطائه', 'الإدارة'),
    'self.view':            ('عرض بياناته وعهده فقط', 'عام'),
}

DEFAULT_ROLES = [
    ('admin', 'مدير النظام', 'كامل الصلاحيات', list(PERMISSIONS)),
    ('hr', 'شؤون الموظفين', 'إنشاء ومتابعة نماذج براءة الذمة',
     ['clearance.create', 'clearance.view_all', 'clearance.manage', 'custody.view_all', 'employees.manage',
      'reports.view', 'email_log.view', 'self.view']),
    ('finance', 'المحاسبة', 'التسوية المالية للنماذج المعلقة',
     ['finance.settle', 'clearance.sign', 'reports.view', 'self.view']),
    ('dept_manager', 'مسؤول قسم', 'توقيع قسمه وإدارة عهد قسمه',
     ['clearance.sign', 'custody.manage', 'custody.import', 'self.view']),
    ('employee', 'موظف / مستلم', 'عرض فقط', ['self.view']),
]


def seed_default_roles(tenant_id):
    """Create the default roles for a tenant (idempotent). Must run inside
    tenant_context(tenant_id)."""
    from extensions import db
    from models import Role, RolePermission
    existing = {r.key for r in Role.query.all()}
    for key, name, desc, perms in DEFAULT_ROLES:
        if key in existing:
            continue
        role = Role(key=key, name=name, description=desc, is_system=True)
        role.permissions = [RolePermission(permission_code=c) for c in perms]
        db.session.add(role)
    db.session.flush()


def current_user():
    return getattr(g, 'user', None)


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        user = current_user()
        if user is None:
            return redirect(url_for('auth.login', next=request.full_path))
        if user.must_change_password and request.endpoint not in (
                'auth.change_password', 'auth.logout'):
            return redirect(url_for('auth.change_password'))
        return f(*a, **kw)
    return wrapper


def permission_required(*codes):
    """Allow if the user has ANY of the given permission codes."""
    def deco(f):
        @wraps(f)
        @login_required
        def wrapper(*a, **kw):
            user = current_user()
            if not any(user.has_perm(c) for c in codes):
                abort(403)
            return f(*a, **kw)
        return wrapper
    return deco


def can_access_department(user, department_id, own_perm, all_perm=None):
    """Department-scoped check: with ``all_perm`` any department; with
    ``own_perm`` only departments the user is linked to."""
    if user is None:
        return False
    if all_perm and user.has_perm(all_perm):
        return True
    return user.has_perm(own_perm) and department_id in user.department_ids


def require_department(department_id, own_perm, all_perm=None):
    if not can_access_department(current_user(), department_id, own_perm, all_perm):
        abort(403)


def superadmin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if getattr(g, 'superadmin', None) is None:
            return redirect(url_for('super.login'))
        return f(*a, **kw)
    return wrapper


def has_perm(code):
    u = current_user()
    return bool(u and u.has_perm(code))
