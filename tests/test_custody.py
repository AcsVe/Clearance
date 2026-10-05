import re

from extensions import db
from models import Custody, AuditLog, Tenant
from tenancy import tenant_context
from tests.conftest import login, make_tenant, make_user, make_dept
from tests.test_clearance import org, client_for, outbox  # noqa: F401  (fixtures/helpers)


def hand(app, user, dept_id, item='حاسوب محمول Dell', recipient='سامي يوسف',
         email='leaver@raed.test', handed_email='handover@raed.test'):
    return client_for(app, user).post('/custody/new', data={
        'department_id': dept_id, 'item_name': item, 'recipient_name': recipient,
        'recipient_email': email, 'handed_by_name': 'أمين المستودع',
        'handed_by_email': handed_email})


def cid_from(r):
    return int(r.headers['Location'].rsplit('/', 1)[1])


def get(org, cid):
    with tenant_context(org['t']):
        db.session.expire_all()
        return db.session.get(Custody, cid)


def test_handover_emails_and_record(app, org):
    r = hand(app, 'itmgr', org['it'])
    assert r.status_code == 302
    c = get(org, cid_from(r))
    assert re.fullmatch(r'CUST-[A-Z0-9]{6}', c.code) and c.status == 'handed'
    assert c.department_name == 'تقنية المعلومات' and c.recipient_user_id == org['emp']
    mails = outbox(app, 'custody_handed')
    assert sorted(tuple(m['to']) for m in mails) == [('it@raed.test', 'handover@raed.test'),
                                                     ('leaver@raed.test',)]
    assert all(f'https://clr.test/custody/{c.id}/print' in m['html'] for m in mails)
    with tenant_context(org['t']):
        assert AuditLog.query.filter_by(action='custody.handed').count() == 1


def test_department_scoping(app, org):
    c_id = cid_from(hand(app, 'itmgr', org['it']))
    assert hand(app, 'itmgr', org['lib']).status_code == 403          # not his department
    lib = client_for(app, 'libmgr')
    assert 'حاسوب محمول' not in lib.get('/custody/').get_data(as_text=True)
    assert lib.get(f'/custody/{c_id}').status_code == 403
    assert lib.post(f'/custody/{c_id}/return').status_code == 403
    assert get(org, c_id).status == 'handed'
    # HR can see every department but cannot hand over or return
    hr = client_for(app, 'hr')
    assert 'حاسوب محمول' in hr.get('/custody/').get_data(as_text=True)
    assert hr.post(f'/custody/{c_id}/return').status_code == 403
    # plain employee: no list, no handover
    emp = client_for(app, 'emp')
    assert emp.get('/custody/').headers['Location'].endswith('/custody/mine')
    assert emp.get('/custody/new').status_code == 403


def test_list_order_handed_first(app, org):
    a = cid_from(hand(app, 'itmgr', org['it'], item='طابعة-1'))
    b = cid_from(hand(app, 'itmgr', org['it'], item='طابعة-2'))
    c = cid_from(hand(app, 'itmgr', org['it'], item='طابعة-3'))
    client_for(app, 'itmgr').post(f'/custody/{c}/return', data={})
    html = client_for(app, 'itmgr').get('/custody/').get_data(as_text=True)
    assert html.index('طابعة-2') < html.index('طابعة-1') < html.index('طابعة-3')


def test_return_flow(app, org):
    c_id = cid_from(hand(app, 'itmgr', org['it']))
    app.config['MAIL_OUTBOX'] = []
    r = client_for(app, 'itmgr').post(f'/custody/{c_id}/return', data={
        'returned_to_name': 'فادي', 'returned_to_email': 'it@raed.test'})   # duplicate of dept email
    assert r.status_code == 302
    c = get(org, c_id)
    assert c.status == 'returned' and c.returned_to_name == 'فادي' and c.returned_at
    mails = outbox(app, 'custody_returned')
    assert len(mails) == 1
    assert sorted(mails[0]['to']) == ['handover@raed.test', 'it@raed.test', 'leaver@raed.test']
    r = client_for(app, 'itmgr').post(f'/custody/{c_id}/return', data={}, follow_redirects=True)
    assert 'مسترجعة مسبقًا' in r.get_data(as_text=True)
    html = client_for(app, 'itmgr').get(f'/custody/{c_id}/print').get_data(as_text=True)
    assert 'تم الاسترجاع' in html and 'فادي' in html and 'صادرة آلياً من نموذج العهد العينية' in html


def test_employee_sees_own(app, org):
    c_id = cid_from(hand(app, 'itmgr', org['it']))
    emp = client_for(app, 'emp')
    assert 'حاسوب محمول' in emp.get('/custody/mine').get_data(as_text=True)
    assert emp.get(f'/custody/{c_id}/print').status_code == 200
    assert client_for(app, 'other').get(f'/custody/{c_id}/print').status_code == 403


def test_validation(app, org):
    r = hand(app, 'itmgr', org['it'], item='', email='not-email')
    assert r.status_code == 400
    with tenant_context(org['t']):
        assert Custody.query.count() == 0


def test_escaping(app, org):
    evil = '<img src=x onerror=alert(1)>'
    c_id = cid_from(hand(app, 'itmgr', org['it'], item=evil, recipient=evil))
    c = client_for(app, 'itmgr')
    for path in ('/custody/', f'/custody/{c_id}', f'/custody/{c_id}/print'):
        html = c.get(path).get_data(as_text=True)
        assert evil not in html and '&lt;img' in html, path
    assert all('<img src=x' not in m['html'] for m in outbox(app))


def test_cross_tenant(app, org):
    c_id = cid_from(hand(app, 'itmgr', org['it']))
    other = make_tenant('other')
    make_user(other, 'intruder', ('admin',))
    c = app.test_client()
    login(c, 'intruder', org='other')
    assert c.get(f'/custody/{c_id}').status_code == 404
    assert c.post(f'/custody/{c_id}/return').status_code == 404
    assert 'حاسوب' not in c.get('/custody/').get_data(as_text=True)
