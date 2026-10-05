import re
import threading

import pytest
from flask import g

from extensions import db
from models import (ClearanceForm, ClearanceResponse, ActionToken, AuditLog, EmailLog, Tenant,
                    User, Department)
from tenancy import tenant_context
from tests.conftest import login, make_tenant, make_dept, make_user


@pytest.fixture
def org(ctx):
    t = make_tenant('raed', 'مدرسة الرائد العربي')
    ids = {'t': t.id}
    ids['it'] = make_dept(t, 'IT', 'تقنية المعلومات', emails='it@raed.test')
    ids['lib'] = make_dept(t, 'LIB', 'المكتبة', emails='lib1@raed.test; lib2@raed.test')
    ids['hr_d'] = make_dept(t, 'HR', 'شؤون الموظفين', 'HR', emails='hr@raed.test')
    ids['fin_d'] = make_dept(t, 'FIN', 'المحاسبة', 'Finance', emails='fin@raed.test')
    ids['hr'] = make_user(t, 'hr', ('hr', 'dept_manager'), (ids['hr_d'],), email='hruser@raed.test')
    ids['fin'] = make_user(t, 'fin', ('finance', 'dept_manager'), (ids['fin_d'],), email='finuser@raed.test')
    ids['it_mgr'] = make_user(t, 'itmgr', ('dept_manager',), (ids['it'],))
    ids['lib_mgr'] = make_user(t, 'libmgr', ('dept_manager',), (ids['lib'],))
    ids['emp'] = make_user(t, 'emp', ('employee',), email='leaver@raed.test')
    ids['other_emp'] = make_user(t, 'other', ('employee',), email='other@raed.test')
    return ids


def client_for(app, username):
    c = app.test_client()
    assert login(c, username).status_code == 302, username
    return c


def create(app, name='أحمد محمود', email='leaver@raed.test'):
    c = client_for(app, 'hr')
    r = c.post('/clearance/new', data={'employee_name': name, 'employee_number': '1042',
                                       'employee_email': email, 'employee_department': 'العلوم',
                                       'resign_date': '2026-09-30'})
    assert r.status_code == 302, r.get_data(as_text=True)
    form_id = int(r.headers['Location'].rsplit('/', 1)[1])
    return c, form_id


def outbox(app, kind=None):
    return [m for m in app.config['MAIL_OUTBOX'] if kind is None or m['kind'] == kind]


def sign(app, username, form_id, dept_id, status='cleared', note=''):
    return client_for(app, username).post(f'/clearance/{form_id}/sign/{dept_id}',
                                          data={'status': status, 'note': note})


def form_of(org, form_id):
    with tenant_context(org['t']):
        db.session.expire_all()
        return db.session.get(ClearanceForm, form_id)


def test_create_form(app, org):
    _, fid = create(app)
    f = form_of(org, fid)
    assert re.fullmatch(r'CLR-\d{4}-[A-Z0-9]{6}', f.code)
    assert f.status == 'in_progress' and f.progress == 0
    assert len(f.responses) == 4 and all(r.status == 'pending' for r in f.responses)
    assert [m['to'] for m in outbox(app, 'clearance_started')] == [['leaver@raed.test']]
    sign_to = sorted(tuple(m['to']) for m in outbox(app, 'clearance_sign_request'))
    assert sign_to == [('it@raed.test',), ('lib1@raed.test', 'lib2@raed.test')]   # not HR/Finance
    assert f.code in outbox(app, 'clearance_started')[0]['html']
    with tenant_context(org['t']):
        assert AuditLog.query.filter_by(action='clearance.created').count() == 1


def test_create_requires_permission(app, org):
    for u in ('itmgr', 'emp', 'fin'):
        assert client_for(app, u).get('/clearance/new').status_code == 403
        assert client_for(app, u).get('/clearance/').status_code == 403


def test_create_validation(app, org):
    c = client_for(app, 'hr')
    r = c.post('/clearance/new', data={'employee_name': '', 'employee_email': 'bad'})
    assert r.status_code == 400
    with tenant_context(org['t']):
        assert ClearanceForm.query.count() == 0


def test_full_workflow_with_held_then_settlement(app, org):
    _, fid = create(app)
    # each manager sees only his own department's line
    c_it = client_for(app, 'itmgr')
    page = c_it.get('/clearance/sign').get_data(as_text=True)
    assert 'أحمد محمود' in page and 'المكتبة' not in page
    assert sign(app, 'itmgr', fid, org['lib']).status_code == 403    # not his department
    assert sign(app, 'itmgr', fid, org['it']).status_code == 302
    r = sign(app, 'libmgr', fid, org['lib'], 'held', '')
    assert r.status_code == 400 and 'سبب التعليق' in r.get_data(as_text=True)
    assert sign(app, 'libmgr', fid, org['lib'], 'held', 'كتابان لم يُعادا').status_code == 302
    assert sign(app, 'hr', fid, org['hr_d']).status_code == 302
    assert form_of(org, fid).progress == 75
    assert not outbox(app, 'clearance_complete')
    assert sign(app, 'fin', fid, org['fin_d']).status_code == 302

    f = form_of(org, fid)
    assert f.status == 'complete' and f.progress == 100 and f.has_held
    done = outbox(app, 'clearance_complete')
    assert sorted(tuple(m['to']) for m in done) == [('fin@raed.test',), ('hr@raed.test',),
                                                     ('leaver@raed.test',)]
    emp_mail = next(m for m in done if m['to'] == ['leaver@raed.test'])
    assert emp_mail['subject'] == 'إشعار: يجب استكمال براءة الذمة'
    assert 'كتابان لم يُعادا' in emp_mail['html'] and 'المكتبة' in emp_mail['html']

    # finance portal
    c_fin = client_for(app, 'fin')
    assert 'أحمد محمود' in c_fin.get('/clearance/finance').get_data(as_text=True)
    r = c_fin.post(f'/clearance/{fid}/settle', data={'note': 'خُصم ثمن الكتابين'})
    assert r.status_code == 302
    f = form_of(org, fid)
    assert f.status == 'closed' and f.is_settled and f.finance_note == 'خُصم ثمن الكتابين'
    settled = outbox(app, 'clearance_settled')
    assert settled[0]['to'] == ['hr@raed.test'] and 'خُصم ثمن الكتابين' in settled[0]['html']

    html = c_fin.get(f'/clearance/{fid}/print').get_data(as_text=True)
    assert 'إثبات تسوية مالية' in html and 'تمت التسوية المالية' in html
    assert f.code in html and 'نموذج ر ع ع - 1 2026' in html and 'خُصم ثمن الكتابين' in html

    # closed: departments can no longer change anything
    r = sign(app, 'libmgr', fid, org['lib'], 'cleared')
    assert r.status_code == 400 and 'الملف مقفل من المحاسبة' in r.get_data(as_text=True)
    assert 'أحمد محمود' not in client_for(app, 'libmgr').get('/clearance/sign').get_data(as_text=True)


def test_fully_cleared_path(app, org):
    _, fid = create(app)
    for user, dept in (('itmgr', 'it'), ('libmgr', 'lib'), ('hr', 'hr_d'), ('fin', 'fin_d')):
        assert sign(app, user, fid, org[dept]).status_code == 302
    f = form_of(org, fid)
    assert f.status == 'complete' and not f.has_held
    emp_mail = next(m for m in outbox(app, 'clearance_complete') if m['to'] == ['leaver@raed.test'])
    assert emp_mail['subject'] == 'إشعار: براءة الذمة جاهزة الآن' and 'خالية' in emp_mail['html']
    assert 'أحمد محمود' not in client_for(app, 'fin').get('/clearance/finance').get_data(as_text=True)
    html = client_for(app, 'hr').get(f'/clearance/{fid}/print').get_data(as_text=True)
    assert 'نموذج براءة الذمة' in html and 'حالة الطلب: مكتمل' in html


def test_outcome_change_after_completion_notifies_again(app, org):
    _, fid = create(app)
    sign(app, 'itmgr', fid, org['it'])
    sign(app, 'libmgr', fid, org['lib'], 'held', 'كتاب')
    sign(app, 'hr', fid, org['hr_d'])
    sign(app, 'fin', fid, org['fin_d'])
    n = len(outbox(app, 'clearance_complete'))
    sign(app, 'libmgr', fid, org['lib'], 'cleared')          # book returned
    later = outbox(app, 'clearance_complete')[n:]
    assert any(m['subject'] == 'إشعار: براءة الذمة جاهزة الآن' for m in later)
    sign(app, 'itmgr', fid, org['it'], 'cleared', 'تعديل ملاحظة')   # no outcome change
    assert len(outbox(app, 'clearance_complete')) == len(later) + n


def _sign_link(app, to):
    m = next(m for m in outbox(app) if m['kind'] in ('clearance_sign_request', 'clearance_reminder')
             and m['to'] == to and 'href' in m['html'])
    link = re.search(r'href="(https://clr\.test/clearance/c/[^"]+)"', m['html']).group(1)
    return link.replace('https://clr.test', '')


def test_sign_token_link(app, org):
    _, fid = create(app)
    path = _sign_link(app, ['it@raed.test'])
    anon = app.test_client().get(path)
    assert anon.status_code == 302 and '/login' in anon.headers['Location']
    assert client_for(app, 'libmgr').get(path).status_code == 403   # wrong department
    assert client_for(app, 'emp').get(path).status_code == 403
    r = client_for(app, 'itmgr').get(path)
    assert r.status_code == 302 and f"/clearance/{fid}/sign/{org['it']}" in r.headers['Location']
    sign(app, 'itmgr', fid, org['it'])
    r = client_for(app, 'itmgr').get(path)                           # used → dead
    assert r.status_code == 302 and r.headers['Location'].endswith('/clearance/sign')
    with tenant_context(org['t']):
        tokens = ActionToken.query.all()
        assert all(len(t.token_hash) == 64 for t in tokens)
        assert path.rsplit('/', 1)[1] not in {t.token_hash for t in tokens}
    assert app.test_client().get('/clearance/c/not-a-real-token').status_code == 404


def test_reminder_only_pending_and_rotates_token(app, org):
    c, fid = create(app)
    old_lib = _sign_link(app, ['lib1@raed.test', 'lib2@raed.test'])
    sign(app, 'itmgr', fid, org['it'])
    app.config['MAIL_OUTBOX'] = []
    r = c.post(f'/clearance/{fid}/action', data={'action': 'remind'}, follow_redirects=True)
    # like sendReminder(): every still-pending department with an email,
    # including HR/Finance lines (only the first request skips those)
    assert 'تم إرسال التذكير لـ 3 أقسام' in r.get_data(as_text=True)
    assert sorted(tuple(m['to']) for m in outbox(app, 'clearance_reminder')) == [
        ('fin@raed.test',), ('hr@raed.test',), ('lib1@raed.test', 'lib2@raed.test')]
    new_lib = _sign_link(app, ['lib1@raed.test', 'lib2@raed.test'])
    assert new_lib != old_lib
    assert client_for(app, 'libmgr').get(old_lib).headers['Location'].endswith('/clearance/sign')
    assert '/sign/' in client_for(app, 'libmgr').get(new_lib).headers['Location']


def test_cancel_and_close(app, org):
    c, fid = create(app)
    c.post(f'/clearance/{fid}/action', data={'action': 'cancel'})
    assert form_of(org, fid).status == 'cancelled'
    assert sign(app, 'itmgr', fid, org['it']).status_code == 400
    assert 'أحمد' not in client_for(app, 'itmgr').get('/clearance/sign').get_data(as_text=True)
    c2, fid2 = create(app, 'سارة')
    c2.post(f'/clearance/{fid2}/action', data={'action': 'close'})
    assert form_of(org, fid2).status == 'closed'
    # employee/manager cannot run HR actions
    r = client_for(app, 'itmgr').post(f'/clearance/{fid2}/action', data={'action': 'cancel'})
    assert r.status_code == 403


def test_send_to_accounting(app, org):
    c, fid = create(app)
    c.post(f'/clearance/{fid}/action', data={'action': 'to_finance', 'note': 'سلفة 200 دينار'})
    assert form_of(org, fid).hr_note == 'سلفة 200 دينار'
    m = outbox(app, 'clearance_to_finance')[0]
    assert m['to'] == ['fin@raed.test'] and 'سلفة 200 دينار' in m['html']
    # no finance department → note still saved, failure shown and logged
    with tenant_context(org['t']):
        db.session.get(Department, org['fin_d']).is_active = False
        db.session.commit()
    r = c.post(f'/clearance/{fid}/action', data={'action': 'to_finance', 'note': 'ملاحظة 2'},
               follow_redirects=True)
    assert 'تعذّر إرسال' in r.get_data(as_text=True)
    assert form_of(org, fid).hr_note == 'ملاحظة 2'
    with tenant_context(org['t']):
        assert EmailLog.query.filter_by(kind='clearance_to_finance', status='skipped').count() == 1


def test_view_permissions(app, org):
    _, fid = create(app)
    with tenant_context(org['t']):
        tenant = db.session.get(Tenant, org['t'])
    make_user(tenant, 'accountant', ('finance',))        # finance role only, no department
    assert client_for(app, 'accountant').get(f'/clearance/{fid}').status_code == 403  # nothing held
    sign(app, 'libmgr', fid, org['lib'], 'held', 'كتاب')
    assert client_for(app, 'accountant').get(f'/clearance/{fid}').status_code == 200
    assert client_for(app, 'other').get(f'/clearance/{fid}/print').status_code == 403
    assert client_for(app, 'emp').get(f'/clearance/{fid}/print').status_code == 200  # own form
    assert 'CLR-' in client_for(app, 'emp').get('/clearance/mine').get_data(as_text=True)
    assert 'CLR-' not in client_for(app, 'other').get('/clearance/mine').get_data(as_text=True)
    assert client_for(app, 'itmgr').get(f'/clearance/{fid}').status_code == 200   # participating dept


def test_cross_tenant(app, org):
    _, fid = create(app)
    path = _sign_link(app, ['it@raed.test'])
    other = make_tenant('other')
    make_user(other, 'intruder', ('admin',))
    make_dept(other, 'IT', 'IT')
    c = app.test_client()
    login(c, 'intruder', org='other')
    assert c.get(f'/clearance/{fid}').status_code == 404
    assert c.get(f'/clearance/{fid}/print').status_code == 404
    assert c.get(path).status_code == 404
    assert 'أحمد' not in c.get('/clearance/').get_data(as_text=True)


def test_escaping_everywhere(app, org):
    evil = '<img src=x onerror=alert(1)>'
    c, fid = create(app, name=evil)
    sign(app, 'libmgr', fid, org['lib'], 'held', '<script>x()</script>')
    for path in ('/clearance/', f'/clearance/{fid}', f'/clearance/{fid}/print'):
        html = c.get(path).get_data(as_text=True)
        assert evil not in html and '&lt;img src=x onerror=alert(1)&gt;' in html, path
        assert '<script>x()</script>' not in html
    for m in outbox(app):           # subjects are plain text; bodies must be escaped
        assert '<img src=x' not in m['html'] and '<script>x()' not in m['html']


def test_concurrent_last_responses_complete_exactly_once(app, org):
    """Two departments answer at the same moment; the row lock guarantees the
    form completes and notifies exactly once."""
    from services import clearance as svc
    for attempt in range(3):
        app.config['MAIL_OUTBOX'] = []
        _, fid = create(app, f'موظف {attempt}')
        sign(app, 'hr', fid, org['hr_d'])
        sign(app, 'fin', fid, org['fin_d'])
        barrier = threading.Barrier(2)
        errors = []

        def respond(user_id, dept_id):
            try:
                with app.test_request_context('/'):
                    with tenant_context(org['t']):
                        g.tenant = db.session.get(Tenant, org['t'])
                        user = db.session.get(User, user_id)
                        barrier.wait()
                        svc.record_response(fid, dept_id, 'cleared', '', user)
                        db.session.remove()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        ts = [threading.Thread(target=respond, args=(org['it_mgr'], org['it'])),
              threading.Thread(target=respond, args=(org['lib_mgr'], org['lib']))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errors
        assert form_of(org, fid).status == 'complete'
        assert len([m for m in outbox(app, 'clearance_complete') if m['to'] == ['leaver@raed.test']]) == 1
