"""إعادة تعيين كلمة مرور أي حساب من الكمبيوتر الذي يعمل عليه النظام، لمن نسي كلمة المرور ولم يصله رابط البريد.

    python ops/reset_password.py        (أو reset_password.bat)

تُكتب كلمة المرور الجديدة مخفية في نافذة الأوامر نفسها، ولا تُرسل ولا تُحفظ إلا مشفّرة في القاعدة. ويُفك قفل الحساب
إن كان مقفلاً بعد محاولات خاطئة، ويُفعَّل إن كان موقوفاً، وتُنهى جلساته المفتوحة.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, load_env, t  # noqa: E402


def accounts():
    """Every account that can sign in: [(kind, id, tenant id, label)] — the organisation users first, the admins of
    each organisation at the top, then the platform super admins."""
    from models import Role, SuperAdmin, Tenant, User, UserRole
    from tenancy import tenant_bypass
    out = []
    with tenant_bypass():
        admin_role = {r.id for r in Role.query.filter_by(key='admin').all()}
        admins = {ur.user_id for ur in UserRole.query.all() if ur.role_id in admin_role}
        tenants = {x.id: x.slug for x in Tenant.query.all()}
        users = sorted(User.query.all(), key=lambda u: (u.id not in admins, u.tenant_id, u.username.lower()))
        for u in users:
            state = []
            if u.id in admins:
                state.append(t('مدير', 'admin'))
            if not u.is_active:
                state.append(t('موقوف', 'disabled'))
            if u.locked_until:
                state.append(t('مقفل', 'locked'))
            out.append(('user', u.id, u.tenant_id,
                        f'{u.username}  ({tenants.get(u.tenant_id, "?")}{" - " + ", ".join(state) if state else ""})'))
        for s in SuperAdmin.query.order_by(SuperAdmin.username).all():
            out.append(('super', s.id, None, f'{s.username}  ({t("المدير العام للنظام - /super/login", "platform super admin - /super/login")})'))
    return out


def set_password(kind, account_id, password):
    """Writes the new password (hashed), unlocks and activates the account and ends its open sessions."""
    from extensions import db
    from models import SuperAdmin, User
    from security import hash_password
    from tenancy import tenant_bypass
    with tenant_bypass():
        acc = db.session.get(SuperAdmin if kind == 'super' else User, account_id)
        if acc is None:
            raise ValueError('account not found')
        acc.password_hash = hash_password(password)
        acc.is_active = True
        acc.failed_logins = 0
        acc.locked_until = None
        acc.session_version = (acc.session_version or 1) + 1
        if kind == 'user':
            acc.must_change_password = False
        db.session.commit()
        return acc.username


def main():
    load_env()
    sys.path.insert(0, BASE)
    os.environ.setdefault('DISABLE_REMINDERS', '1')
    from app import create_app
    from first_org import ask_password
    app = create_app({'DISABLE_REMINDERS': True})
    with app.app_context():
        found = accounts()
        if not found:
            print(t('  لا توجد حسابات بعد. شغّل 1_setup.bat أولاً.', '  There are no accounts yet. Run 1_setup.bat first.'))
            return 1
        print(t('\n  الحسابات:', '\n  Accounts:'))
        for i, (_k, _id, _tid, label) in enumerate(found, 1):
            print(f'   {i}. {label}')
        while True:
            raw = input(t('\n  رقم الحساب [1]: ', '\n  Account number [1]: ')).strip() or '1'
            if raw.isdigit() and 1 <= int(raw) <= len(found):
                break
            print(t('   - اكتب رقماً من القائمة', '   - type a number from the list'))
        kind, acc_id, _tid, label = found[int(raw) - 1]
        username = label.split()[0]
        print(t(f'\n  كلمة المرور الجديدة للحساب: {username}', f'\n  New password for: {username}'))
        pw = ask_password(username)
        set_password(kind, acc_id, pw)
        print(t(f'\n  ✓ تغيّرت كلمة المرور، وفُك القفل. اسم الدخول: {username}',
                f'\n  ✓ Password changed and the account unlocked. Sign-in name: {username}'))
        url = os.environ.get('BASE_URL', '')
        print(t(f'  صفحة الدخول: {url}{"/super/login" if kind == "super" else "/login"}',
                f'  Sign in at: {url}{"/super/login" if kind == "super" else "/login"}'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
