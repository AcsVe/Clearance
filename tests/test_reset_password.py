"""The password reset run on the computer itself (reset_password.bat): any account gets a new password, is
unlocked and activated, and can sign in with it."""
import os
import sys
from datetime import datetime, timedelta

from tests.conftest import login, make_tenant, make_user

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ops'))


def test_reset_unlocks_and_signs_in(app):
    import reset_password as rp
    from extensions import db
    from models import SuperAdmin, User
    from security import hash_password
    from tenancy import tenant_bypass
    with app.app_context():
        ten = make_tenant('ras', 'مدرسة')
        uid = make_user(ten, 'admin', ('admin',), email='admin@ras.test')
        make_user(ten, 'emp', ('employee',))
        with tenant_bypass():
            u = db.session.get(User, uid)
            u.locked_until = datetime.utcnow() + timedelta(hours=1)
            u.failed_logins = 9
            u.is_active = False
            db.session.add(SuperAdmin(username='root', password_hash=hash_password('Unused-pass-123')))
            db.session.commit()
        found = rp.accounts()
        assert [k for k, *_ in found] == ['user', 'user', 'super']
        assert found[0][1] == uid and found[0][3].startswith('admin')        # the organisation admin is listed first
        rp.set_password('user', uid, 'NewPassw0rd2026')
        with tenant_bypass():
            u = db.session.get(User, uid)
            assert u.is_active and u.locked_until is None and u.failed_logins == 0
    r = login(app.test_client(), 'admin', 'NewPassw0rd2026', org='ras', follow_redirects=False)
    assert r.status_code in (302, 303) and '/login' not in r.headers.get('Location', '')
