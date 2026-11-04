"""اللغة الإنجليزية: كل صفحة تُعرض من اليسار إلى اليمين وبلا نص عربي في الواجهة (البيانات المحفوظة بالعربية مستثناة)."""
import re

from extensions import db
from models import ClearanceForm, Custody, Department, EmailLog, Employee, Role, Tenant, User
from tenancy import tenant_context
from tests.conftest import login, make_dept

AR_WORD = re.compile('[؀-ۿ][؀-ۿً-ْـ]*')


def _visible_text(html):
    html = re.sub(r'<(script|style)\b.*?</\1>', ' ', html, flags=re.S)
    html = re.sub(r'<option[^>]*>.*?</option>', ' ', html, flags=re.S)   # lists of stored names
    return re.sub(r'<[^>]+>', ' ', html)


def _data_words(tenant_id):
    """Arabic words that belong to stored data (names of people, departments, the organization)."""
    words = set()
    with tenant_context(tenant_id):
        tn = db.session.get(Tenant, tenant_id)
        texts = [tn.name_ar, tn.form_footer or 'نموذج ر ع ع - 1 2026', tn.email_footer or '']
        texts += [d.name for d in Department.query.all()] + [u.full_name for u in User.query.all()]
        texts += [e.full_name for e in Employee.query.all()] + [c.item for c in Custody.query.all()]
        texts += [f.employee_name for f in ClearanceForm.query.all()] + [r.name for r in Role.query.all() if not r.is_system]
        texts += [f.department_name or '' for f in ClearanceForm.query.all()]
        texts += [m.subject or '' for m in EmailLog.query.all()]          # emails are written in Arabic
    for t in texts:
        words.update(AR_WORD.findall(t or ''))
        words.update(AR_WORD.findall((t or '')[:1]))    # avatar initials
    return words


def test_every_page_in_english(app, two_tenants):
    t = two_tenants
    a = db.session.get(Tenant, t['a'])
    hr = make_dept(a, 'HR', 'شؤون الموظفين', 'HR', emails='hr@a.test')
    make_dept(a, 'FIN', 'المحاسبة', 'Finance', emails='fin@a.test')
    with tenant_context(t['a']):
        e = Employee(full_name='منى الموظفة', employee_number='77', email='mona@a.test', department_id=t['dept_a'])
        db.session.add(e)
        db.session.commit()
        db.session.add(Custody(ref_code='CUST-EN1', item='حاسوب', recipient_name='منى الموظفة', employee_id=e.id,
                               department_id=t['dept_a'], status='handed'))
        db.session.commit()
        emp_id = e.id
    c = app.test_client()
    login(c, 'admin', org='a')
    c.post('/clearance/new', data={'employee_id': emp_id, 'resign_date': '2026-11-30', 'departments': [t['dept_a'], hr]})
    c.get('/home')                     # shows (and clears) the Arabic message of the step above
    with tenant_context(t['a']):
        form = ClearanceForm.query.one()
        token = form.responses[0].token
        fid, cid = form.id, Custody.query.one().id
    r = c.get('/lang/en?next=/home')
    assert r.status_code == 302 and r.headers['Location'].endswith('/home') and 'lang=en' in r.headers.get('Set-Cookie', '')
    allowed = _data_words(t['a'])
    pages = ['/home', '/clearance/', '/clearance/new', f'/clearance/{fid}', f'/clearance/{fid}/print', '/departments/portal',
             '/finance', '/custody/', '/my', f'/custody/receipt?ids={cid}', f'/custody/{cid}', '/employees/', f'/employees/{emp_id}',
             '/admin/departments', '/admin/departments/new', f'/admin/departments/{t["dept_a"]}', '/admin/users', '/admin/users/new',
             f'/admin/users/{t["mgr_a"]}', '/admin/roles', '/admin/roles/new', '/admin/settings', '/admin/email-log', '/admin/audit',
             '/change-password']
    for url in pages:
        r = c.get(url)
        assert r.status_code == 200, url
        html = r.get_data(as_text=True)
        assert '<html lang="en" dir="ltr">' in html, url
        left = sorted(set(AR_WORD.findall(_visible_text(html))) - allowed - {'العربية'})
        assert not left, (url, left[:15])
    # the signing page from an email link, and the sign-in page after signing out, remember the language
    anon = app.test_client()
    anon.set_cookie('lang', 'en')
    html = anon.get(f'/sign/{token}').get_data(as_text=True)
    assert 'dir="ltr"' in html and 'Clearance signature' in html
    html = anon.get('/login').get_data(as_text=True)
    assert 'dir="ltr"' in html and 'Sign in' in html and 'العربية' in html
    # back to Arabic
    c.get('/lang/ar?next=/home')
    assert 'dir="rtl"' in c.get('/home').get_data(as_text=True)
    assert c.get('/lang/en?next=//evil.example').headers['Location'].endswith('/')   # no open redirect


def test_messages_follow_the_language(app, two_tenants):
    c = app.test_client()
    c.set_cookie('lang', 'en')
    r = c.post('/login', data={'username': 'admin', 'password': 'wrong-pass-1', 'org': 'a'})
    assert 'Wrong user name or password' in r.get_data(as_text=True)
    login(c, 'admin', org='a')
    r = c.post('/admin/departments/new', data={'code': '', 'name': '', 'dept_type': 'normal'})
    assert 'The department name is required' in r.get_data(as_text=True)
