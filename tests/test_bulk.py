"""Bulk select / delete / activate on the lists, the clickable dashboard and the users card view."""
from extensions import db
from models import User, Role, Department, Tenant
from tenancy import tenant_context
from tests.conftest import login, make_user, make_dept


def _admin(app):
    c = app.test_client()
    login(c, 'admin', org='a')
    return c


def test_users_bulk_protects_self_and_tenant(app, two_tenants):
    t = two_tenants
    c = _admin(app)
    r = c.post('/admin/users/bulk', data={'action': 'deactivate',
                                          'ids': [t['admin_a'], t['emp_a'], t['secret_b']]})
    assert r.status_code == 302
    with tenant_context(t['a']):
        assert db.session.get(User, t['admin_a']).is_active          # never yourself
        assert not db.session.get(User, t['emp_a']).is_active
    with tenant_context(t['b']):
        assert db.session.get(User, t['secret_b']).is_active          # other tenant untouched
    c.post('/admin/users/bulk', data={'action': 'activate', 'ids': [t['emp_a']]})
    c.post('/admin/users/bulk', data={'action': 'delete', 'ids': [t['emp_a'], t['admin_a']]})
    with tenant_context(t['a']):
        assert db.session.get(User, t['emp_a']) is None
        assert db.session.get(User, t['admin_a']) is not None


def test_users_bulk_keeps_last_admin(app, two_tenants):
    t = two_tenants
    a = db.session.get(Tenant, t['a'])
    second = make_user(a, 'admin2', ('admin',))
    third = make_user(a, 'admin3', ('admin',))
    c = _admin(app)
    # admin is signed in, so admin2/admin3 may go — admin itself stays as the last administrator
    c.post('/admin/users/bulk', data={'action': 'deactivate', 'ids': [second, third]})
    with tenant_context(t['a']):
        assert not db.session.get(User, second).is_active and not db.session.get(User, third).is_active
        assert db.session.get(User, t['admin_a']).is_active


def test_departments_bulk(app, two_tenants):
    t = two_tenants
    a = db.session.get(Tenant, t['a'])
    hr1 = make_dept(a, 'HR', 'شؤون الموظفين', 'HR')
    hr2 = make_dept(a, 'HR2', 'شؤون 2', 'HR')
    c = _admin(app)
    c.post('/admin/departments/bulk', data={'action': 'deactivate', 'ids': [hr2]})
    # a second active HR department is refused on bulk activate
    r = c.post('/admin/departments/bulk', data={'action': 'activate', 'ids': [hr2]}, follow_redirects=True)
    assert 'يوجد قسم فعّال من النوع نفسه' in r.get_data(as_text=True)
    c.post('/admin/departments/bulk', data={'action': 'delete', 'ids': [t['dept_a2'], hr2, t['dept_b']]})
    with tenant_context(t['a']):
        assert db.session.get(Department, t['dept_a2']) is None
        assert db.session.get(Department, hr2) is None
        assert db.session.get(Department, hr1) is not None
    with tenant_context(t['b']):
        assert db.session.get(Department, t['dept_b']) is not None


def test_roles_bulk_keeps_system_and_used(app, two_tenants):
    t = two_tenants
    c = _admin(app)
    c.post('/admin/roles/new', data={'name': 'دور مؤقت', 'perms': ['self.view']})
    with tenant_context(t['a']):
        custom = Role.query.filter_by(name='دور مؤقت').one().id
        sysrole = Role.query.filter_by(key='employee').one().id
    c.post('/admin/roles/bulk', data={'action': 'delete', 'ids': [custom, sysrole]})
    with tenant_context(t['a']):
        assert db.session.get(Role, custom) is None
        assert db.session.get(Role, sysrole) is not None


def test_dashboard_and_cards_render(app, two_tenants):
    c = _admin(app)
    home = c.get('/home').get_data(as_text=True)
    assert 'class="kpi' in home and 'الحسابات الفعّالة' in home and 'f=' in home
    page = c.get('/admin/users').get_data(as_text=True)
    assert 'data-cards' in page and 'data-table' in page and 'براءة الذمة:' in page
    assert 'secretuser' not in page                                    # other tenant's user
    assert c.get('/admin/departments').status_code == 200
    assert c.get('/admin/roles').status_code == 200
    assert c.get('/admin/email-log').status_code == 200
    assert c.get('/admin/audit').status_code == 200


def test_employee_dashboard_has_no_admin_numbers(app, two_tenants):
    c = app.test_client()
    login(c, 'emp', org='a')
    page = c.get('/home').get_data(as_text=True)
    assert 'الحسابات الفعّالة' not in page


def test_settings_logo_removal_and_graph_status(app, two_tenants):
    import io
    from extensions import db
    from models import Tenant
    from tenancy import tenant_bypass
    keys = ('MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET')
    saved = {k: app.config.get(k) for k in keys}
    app.config.update(MS_TENANT_ID='tid-value', MS_CLIENT_ID='', MS_CLIENT_SECRET='very-secret-value')
    c = _admin(app)
    try:
        page = c.get('/admin/settings').get_data(as_text=True)
    finally:
        app.config.update(saved)
    assert 'Microsoft Graph' in page and 'غير مضبوط' in page
    assert 'very-secret-value' not in page and 'tid-value' not in page        # never the values
    assert 'brand/system.svg' in page and 'remove_logo_primary' not in page   # no logo yet: system icon
    png = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
           b'\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')
    form = {'name_ar': 'مدرسة أ', 'accent_color': '#0C67EC'}
    r = c.post('/admin/settings', data={**form, 'logo_primary': (io.BytesIO(png), 'logo.png')},
               content_type='multipart/form-data')
    assert r.status_code == 302
    with app.app_context(), tenant_bypass():
        assert db.session.get(Tenant, two_tenants['a']).logo_primary
    assert 'remove_logo_primary' in c.get('/admin/settings').get_data(as_text=True)
    assert c.post('/admin/settings', data={**form, 'remove_logo_primary': '1'}).status_code == 302
    with app.app_context(), tenant_bypass():
        assert db.session.get(Tenant, two_tenants['a']).logo_primary is None
    home = c.get('/home').get_data(as_text=True)
    assert 'brand/system.svg' in home
