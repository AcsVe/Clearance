"""From the employee card: hand custody, start clearance, return custody; clearance for several employees."""
from extensions import db
from models import Employee, ClearanceForm, Custody, Tenant
from tenancy import tenant_context
from tests.conftest import login
from tests.test_workflow import _setup


def _people(t):
    with tenant_context(t['a']):
        a1 = Employee(full_name='سامي علي', employee_number='100', email='sami@a.test', department_id=t['dept_a'],
                      job_title='فني', is_active=True)
        a2 = Employee(full_name='ريم حسن', employee_number='101', email='reem@a.test', is_active=True)
        a3 = Employee(full_name='بلا بريد', employee_number='102', is_active=True)
        db.session.add_all([a1, a2, a3])
        db.session.commit()
        return a1.id, a2.id, a3.id


def test_card_hands_custody_and_returns_it(app, two_tenants):
    t = _setup(two_tenants)
    sami, _reem, _x = _people(t)
    c = app.test_client()
    login(c, 'admin', org='a')
    card = c.get(f'/employees/{sami}').get_data(as_text=True)
    assert 'تسليم عهدة له' in card and 'بدء براءة ذمة' in card
    page = c.get(f'/custody/?employee_id={sami}&next=/employees/{sami}').get_data(as_text=True)
    assert f'<option value="{sami}" data-name="سامي علي" data-email="sami@a.test" selected>' in page
    assert 'value="/employees/' in page and 'تسليم عهدة إلى سامي علي' in page
    r = c.post('/custody/new', data={'item': 'حاسوب محمول', 'quantity': 1, 'employee_id': sami,
                                     'recipient_name': 'سامي علي', 'department_id': t['dept_a'], 'notify': '0',
                                     'next': f'/employees/{sami}'})
    assert r.status_code == 302 and r.headers['Location'].endswith(f'/employees/{sami}')
    r = c.post('/custody/new', data={'item': 'x', 'employee_id': sami, 'recipient_name': 'سامي علي',
                                     'department_id': t['dept_a'], 'notify': '0', 'next': 'https://evil.test/'})
    assert 'evil' not in r.headers['Location']                       # only paths inside the system
    with tenant_context(t['a']):
        cid = Custody.query.filter_by(item='حاسوب محمول').one().id
    card = c.get(f'/employees/{sami}').get_data(as_text=True)
    assert f'/custody/{cid}/return' in card and 'data-bulk-for="t-emp-cust"' in card
    c.post(f'/custody/{cid}/return', headers={'Referer': f'/employees/{sami}'})
    with tenant_context(t['a']):
        assert db.session.get(Custody, cid).status == 'returned'


def test_card_starts_clearance_with_details(app, two_tenants):
    t = _setup(two_tenants)
    sami, _reem, _x = _people(t)
    with tenant_context(t['a']):
        db.session.add(Custody(ref_code='CUST-T1', item='مفتاح المختبر', quantity=1, status='handed',
                               employee_id=sami, recipient_name='سامي علي', department_id=t['dept_a']))
        db.session.commit()
    c = app.test_client()
    login(c, 'hr1', org='a')
    page = c.get(f'/clearance/new?employee_id={sami}').get_data(as_text=True)
    assert 'value="سامي علي"' in page and 'value="sami@a.test"' in page and 'value="فني"' in page
    assert 'مفتاح المختبر' in page
    assert c.get('/clearance/new?employee_id=999999').status_code == 404


def test_clearance_for_several_employees(app, two_tenants):
    t = _setup(two_tenants)
    sami, reem, nomail = _people(t)
    c = app.test_client()
    login(c, 'hr1', org='a')
    assert 'بدء براءة ذمة' in c.get('/employees/').get_data(as_text=True)
    page = c.post('/clearance/new-many', data={'ids': [sami, reem, nomail]}).get_data(as_text=True)
    assert 'سامي علي' in page and 'ريم حسن' in page and 'بلا بريد' in page
    r = c.post('/clearance/new-many', data={'ids': [sami, reem], 'step': 'create', 'resign_date': '2026-12-31',
                                            'departments': [t['dept_a'], t['hr_dept']]})
    assert r.status_code == 302
    with tenant_context(t['a']):
        forms = ClearanceForm.query.all()
        assert sorted(f.employee_id for f in forms) == sorted([sami, reem])
        assert all(f.resign_date.isoformat() == '2026-12-31' for f in forms)
        assert all(len(f.responses) == 2 for f in forms)
    # a second run skips those who already have an open form
    r = c.post('/clearance/new-many', data={'ids': [sami, nomail], 'step': 'create', 'departments': [t['dept_a']]},
               follow_redirects=True)
    assert 'له نموذج مفتوح' in r.get_data(as_text=True)
    with tenant_context(t['a']):
        assert ClearanceForm.query.count() == 3
    # no department chosen: the page comes back with the message
    r = c.post('/clearance/new-many', data={'ids': [reem], 'step': 'create'})
    assert r.status_code == 400 and 'اختر قسمًا' in r.get_data(as_text=True)


_ = Tenant


def test_other_organisation_employees_are_not_reached(app, two_tenants):
    t = _setup(two_tenants)
    with tenant_context(t['b']):
        other = Employee(full_name='من منظمة أخرى', is_active=True)
        db.session.add(other)
        db.session.commit()
        oid = other.id
    c = app.test_client()
    login(c, 'hr1', org='a')
    r = c.post('/clearance/new-many', data={'ids': [oid]})
    assert r.status_code == 302                                   # nothing of tenant B is listed
    assert c.get(f'/clearance/new?employee_id={oid}').status_code == 404
    page = c.get(f'/custody/?employee_id={oid}').get_data(as_text=True)
    assert 'من منظمة أخرى' not in page
