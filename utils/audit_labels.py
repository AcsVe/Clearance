"""عرض سجل التدقيق بعبارات عربية واضحة بدل رموز الإجراءات والتفاصيل الخام."""

ACTIONS = {
    'auth.login': 'تسجيل دخول', 'auth.logout': 'تسجيل خروج', 'auth.locked': 'قفل الحساب بعد محاولات خاطئة',
    'auth.password_changed': 'تغيير كلمة المرور', 'auth.password_reset': 'تعيين كلمة مرور من رابط البريد',
    'auth.reset_requested': 'طلب رابط استعادة كلمة المرور',
    'clearance.created': 'إنشاء نموذج براءة ذمة', 'clearance.responded': 'ردّ قسم على براءة الذمة',
    'clearance.completed': 'اكتمال نموذج براءة الذمة', 'clearance.reminded': 'تذكير الأقسام المتأخرة',
    'clearance.sent_to_finance': 'إرسال إلى المحاسبة', 'clearance.settled': 'تسوية مالية وإقفال',
    'clearance.deleted': 'حذف نموذج براءة ذمة',
    'custody.handed': 'تسليم عهدة', 'custody.returned': 'استرجاع عهدة', 'custody.return_undone': 'التراجع عن استرجاع عهدة',
    'custody.updated': 'تعديل عهدة', 'custody.deleted': 'حذف عهدة', 'custody.imported': 'استيراد عهد',
    'department.created': 'إضافة قسم', 'department.deleted': 'حذف قسم',
    'employee.created': 'إضافة موظف', 'employee.deleted': 'حذف موظف', 'employee.imported': 'استيراد موظفين',
    'role.saved': 'حفظ دور وصلاحياته', 'role.deleted': 'حذف دور',
    'settings.updated': 'تعديل الإعدادات', 'sheet.imported': 'استيراد بيانات Google Sheet القديمة',
    'email_log.acknowledged': 'الاطلاع على أخطاء البريد',
    'tenant.created': 'إنشاء منظمة', 'tenant.updated': 'تعديل منظمة',
    'user.created': 'إضافة مستخدم', 'user.created_from_employee': 'إنشاء حساب دخول لموظف', 'user.updated': 'تعديل مستخدم',
    'user.deleted': 'حذف مستخدم', 'user.unlocked': 'فك قفل حساب', 'user.sessions_revoked': 'إنهاء جلسات مستخدم',
    'user.password_set_by_superadmin': 'كلمة مرور جديدة من المدير العام',
}
GROUPS = {'auth': 'الدخول', 'clearance': 'براءة الذمة', 'custody': 'العهد', 'department': 'الأقسام', 'employee': 'الموظفون',
          'role': 'الأدوار', 'settings': 'الإعدادات', 'sheet': 'الاستيراد', 'email_log': 'البريد', 'tenant': 'المنظمات',
          'user': 'المستخدمون'}
ENTITIES = {'user': 'مستخدم', 'clearance': 'نموذج', 'custody': 'عهدة', 'department': 'قسم', 'employee': 'موظف',
            'role': 'دور', 'tenant': 'منظمة', 'settings': 'الإعدادات'}
KEYS = {'department': 'القسم', 'departments': 'عدد الأقسام', 'employee': 'الموظف', 'ref': 'الرقم المرجعي', 'status': 'الحالة',
        'count': 'العدد', 'added': 'أُضيف', 'updated': 'حُدِّث', 'skipped': 'تُرك', 'sent': 'أُرسل', 'held': 'معلّق',
        'item': 'العهدة', 'recipient': 'المستلم', 'name': 'الاسم', 'username': 'اسم المستخدم', 'roles': 'الأدوار',
        'permissions': 'الصلاحيات', 'code': 'الرمز', 'problems': 'ملاحظات', 'logos': 'الشعارات', 'by_admin': 'بواسطة المدير',
        'actor': 'المنفّذ', 'note': 'ملاحظة'}
VALUES = {'cleared': 'خالية الذمة', 'held': 'معلّق', 'pending': 'قيد الانتظار', 'returned': 'مسترجعة', 'handed': 'مسلّمة'}


# ── English (shown when the interface is in English) ──────────────────────────
ACTIONS_EN = {
    'auth.login': 'Sign in', 'auth.logout': 'Sign out', 'auth.locked': 'Account locked after wrong attempts',
    'auth.password_changed': 'Password changed', 'auth.password_reset': 'Password set from the email link',
    'auth.reset_requested': 'Password reset link requested',
    'clearance.created': 'Clearance form created', 'clearance.responded': 'Department replied to a clearance',
    'clearance.completed': 'Clearance form completed', 'clearance.reminded': 'Late departments reminded',
    'clearance.sent_to_finance': 'Sent to Finance', 'clearance.settled': 'Financial settlement and closing',
    'clearance.deleted': 'Clearance form deleted',
    'custody.handed': 'Item handed over', 'custody.returned': 'Item returned', 'custody.return_undone': 'Return undone',
    'custody.updated': 'Item edited', 'custody.deleted': 'Item deleted', 'custody.imported': 'Custody imported',
    'department.created': 'Department added', 'department.deleted': 'Department deleted',
    'employee.created': 'Employee added', 'employee.deleted': 'Employee deleted', 'employee.imported': 'Employees imported',
    'role.saved': 'Role and permissions saved', 'role.deleted': 'Role deleted',
    'settings.updated': 'Settings changed', 'sheet.imported': 'Old Google Sheet data imported',
    'email_log.acknowledged': 'Mail errors read',
    'tenant.created': 'Organization created', 'tenant.updated': 'Organization edited',
    'user.created': 'User added', 'user.created_from_employee': 'Sign-in account created for an employee', 'user.updated': 'User edited',
    'user.deleted': 'User deleted', 'user.unlocked': 'Account unlocked', 'user.sessions_revoked': 'User sessions ended',
    'user.password_set_by_superadmin': 'New password from the super administrator',
}
GROUPS_EN = {'auth': 'Sign-in', 'clearance': 'Clearance', 'custody': 'Custody', 'department': 'Departments',
             'employee': 'Employees', 'role': 'Roles', 'settings': 'Settings', 'sheet': 'Import', 'email_log': 'Mail',
             'tenant': 'Organizations', 'user': 'Users'}
ENTITIES_EN = {'user': 'User', 'clearance': 'Form', 'custody': 'Item', 'department': 'Department', 'employee': 'Employee',
               'role': 'Role', 'tenant': 'Organization', 'settings': 'Settings'}
KEYS_EN = {'department': 'Department', 'departments': 'Departments', 'employee': 'Employee', 'ref': 'Reference', 'status': 'Status',
           'count': 'Count', 'added': 'Added', 'updated': 'Updated', 'skipped': 'Skipped', 'sent': 'Sent', 'held': 'On hold',
           'item': 'Item', 'recipient': 'Recipient', 'name': 'Name', 'username': 'User name', 'roles': 'Roles',
           'permissions': 'Permissions', 'code': 'Code', 'problems': 'Notes', 'logos': 'Logos', 'by_admin': 'By the administrator',
           'actor': 'Done by', 'note': 'Note'}
VALUES_EN = {'cleared': 'Cleared', 'held': 'On hold', 'pending': 'Pending', 'returned': 'Returned', 'handed': 'Handed over'}


def _en():
    from utils.i18n import lang
    return lang() == 'en'


def _v(x):
    if isinstance(x, bool):
        return ('Yes' if x else 'No') if _en() else ('نعم' if x else 'لا')
    if isinstance(x, str):
        return (VALUES_EN if _en() else VALUES).get(x, x)
    return x


def _sep():
    return ', ' if _en() else '، '


def action_label(code):
    return (ACTIONS_EN if _en() else ACTIONS).get(code or '', code or '')


def group_label(code):
    return (GROUPS_EN if _en() else GROUPS).get((code or '').split('.')[0], '')


def groups_local():
    """[(code, label)] sorted by label, in the interface language."""
    g = GROUPS_EN if _en() else GROUPS
    return sorted(g.items(), key=lambda kv: kv[1])


def actor_label(actor):
    """'النظام' and 'مدير عام: x' are stored in Arabic; people's names stay as they are."""
    if not _en() or not actor:
        return actor or ''
    if actor == 'النظام':
        return 'System'
    if actor.startswith('مدير عام: '):
        return 'Super administrator: ' + actor[len('مدير عام: '):]
    return actor


def entity_label(entity_type, entity_id):
    if not entity_type:
        return ''
    names = ENTITIES_EN if _en() else ENTITIES
    name = names.get(entity_type, entity_type)
    if not entity_id:
        return name
    return f'{name} no. {entity_id}' if _en() else f'{name} رقم {entity_id}'


def details_lines(details):
    """[(title, value)] in plain words; lists are joined with commas."""
    keys = KEYS_EN if _en() else KEYS
    out = []
    for k, v in (details or {}).items():
        if isinstance(v, (list, tuple)):
            v = _sep().join(str(_v(x)) for x in v)
        elif isinstance(v, dict):
            v = _sep().join(f'{keys.get(a, a)}: {_v(b)}' for a, b in v.items())
        else:
            v = _v(v)
        out.append((keys.get(k, k.replace('_', ' ')), v))
    return out
