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


def _v(x):
    if isinstance(x, bool):
        return 'نعم' if x else 'لا'
    return VALUES.get(x, x) if isinstance(x, str) else x


def action_label(code):
    return ACTIONS.get(code or '', code or '')


def group_label(code):
    return GROUPS.get((code or '').split('.')[0], '')


def entity_label(entity_type, entity_id):
    if not entity_type:
        return ''
    return f'{ENTITIES.get(entity_type, entity_type)} رقم {entity_id}' if entity_id else ENTITIES.get(entity_type, entity_type)


def details_lines(details):
    """[(عنوان، قيمة)] مقروءة؛ القوائم تُجمع بفواصل."""
    out = []
    for k, v in (details or {}).items():
        if isinstance(v, (list, tuple)):
            v = '، '.join(str(_v(x)) for x in v)
        elif isinstance(v, dict):
            v = '، '.join(f'{KEYS.get(a, a)}: {_v(b)}' for a, b in v.items())
        else:
            v = _v(v)
        out.append((KEYS.get(k, k.replace('_', ' ')), v))
    return out
