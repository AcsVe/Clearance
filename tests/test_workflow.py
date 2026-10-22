"""Employees roster, the clearance workflow (create → sign → complete → finance) and custody."""
import io
import re
from datetime import timedelta

from extensions import db
from models import (Tenant, Employee, ClearanceForm, ClearanceResponse, Custody, User, Department)
from models.core import utcnow
from tenancy import tenant_context
from tests.conftest import login, make_user, make_dept


def _setup(two_tenants):
    t = two_tenants
    a = db.session.get(Tenant, t['a'])
    t['hr_dept'] = make_dept(a, 'HR', 'شؤون الموظفين', 'HR', emails='hr@a.test')
    t['fin_dept'] = make_dept(a, 'FIN', 'المحاسبة', 'Finance', emails='fin@a.test')
    with tenant_context(t['a']):
        db.session.get(Department, t['dept_a']).emails = 'it@a.test'
        db.session.get(Department, t['dept_a2']).emails = 'lib@a.test'
        db.session.commit()
    t['hr'] = make_user(a, 'hr1', ('hr',), (t['hr_dept'],), email='hr1@a.test')
    t['fin'] = make_user(a, 'fin1', ('finance',), (t['fin_dept'],), email='fin1@a.test')
    return t


def _client(app, username):
    c = app.test_client()
    login(c, username, org='a')
    return c


def test_employees_add_import_and_accounts(app, two_tenants):
    t = _setup(two_tenants)
    c = _client(app, 'hr1')
    c.post('/employees/new', data={'full_name': 'سامي علي', 'employee_number': '100', 'email': 'sami@a.test',
                                   'department_id': t['dept_a']})
    text = ('الاسم,الرقم الوظيفي,البريد,القسم,المسمى الوظيفي\n'
            'ريم حسن,101,reem@a.test,المكتبه أ,أمينة مكتبة\n'
            'سامي علي المعدل,100,,تقنية المعلومات أ,فني\n'
            'بلا قسم,102,x@a.test,قسم غير موجود,\n')
    r = c.post('/employees/import', data={'text': text}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert 'أُضيف 2' in page and 'قسم غير موجود' in page
    with tenant_context(t['a']):
        reem = Employee.query.filter_by(employee_number='101').one()
        assert reem.department_id == t['dept_a2']            # «المكتبه أ» matched «المكتبة أ»
        assert Employee.query.filter_by(employee_number='100').one().full_name == 'سامي علي'   # not updated
    c.post('/employees/import', data={'text': text, 'update': '1'})
    with tenant_context(t['a']):
        sami = Employee.query.filter_by(employee_number='100').one()
        assert sami.full_name == 'سامي علي المعدل' and sami.email == 'sami@a.test' and sami.job_title == 'فني'
        ids = [e.id for e in Employee.query.all()]
    # Excel upload
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(['name', 'number', 'email'])
    wb.active.append(['Excel Person', '200', 'xl@a.test'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    c.post('/employees/import', data={'file': (buf, 'staff.xlsx')}, content_type='multipart/form-data')
    with tenant_context(t['a']):
        assert Employee.query.filter_by(employee_number='200').one().email == 'xl@a.test'
    # HR has no users.manage → may not create accounts; the admin may
    assert c.post('/employees/bulk', data={'action': 'accounts', 'ids': ids}).status_code == 403
    admin = _client(app, 'admin')
    admin.post('/employees/bulk', data={'action': 'accounts', 'ids': ids, 'send_reset': '1'})
    with tenant_context(t['a']):
        reem = Employee.query.filter_by(employee_number='101').one()
        assert reem.user and reem.user.username == 'reem' and reem.user.has_perm('self.view')
    assert any(m['to'] == ['reem@a.test'] for m in app.config['MAIL_OUTBOX'])


def test_clearance_full_flow(app, two_tenants):
    t = _setup(two_tenants)
    a = db.session.get(Tenant, t['a'])
    with tenant_context(t['a']):
        emp = Employee(full_name='منى الموظفة', employee_number='77', email='mona@a.test', department_id=t['dept_a'])
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
        db.session.add(Custody(ref_code='CUST-T1', item='حاسوب', recipient_name='منى', employee_id=emp_id,
                               department_id=t['dept_a'], status='handed'))
        db.session.commit()
    hr = _client(app, 'hr1')
    app.config['MAIL_OUTBOX'] = []
    r = hr.post('/clearance/new', data={'employee_id': emp_id, 'resign_date': '2026-10-30',
                                        'departments': [t['dept_a'], t['dept_a2'], t['hr_dept'], t['fin_dept']]})
    assert r.status_code == 302
    with tenant_context(t['a']):
        form = ClearanceForm.query.one()
        assert form.employee_name == 'منى الموظفة' and form.department_name == 'تقنية المعلومات أ'
        assert re.fullmatch(r'CLR-\d{4}-[A-Z0-9]{6}', form.ref_code)
        resp = {r.department_id: r for r in form.responses}
        it_token, lib_token = resp[t['dept_a']].token, resp[t['dept_a2']].token
        hr_resp, fin_resp = resp[t['hr_dept']].id, resp[t['fin_dept']].id
        form_id = form.id
    to = [m['to'] for m in app.config['MAIL_OUTBOX']]
    assert ['mona@a.test'] in to and ['it@a.test'] in to and ['lib@a.test'] in to
    assert ['hr@a.test'] not in to and ['fin@a.test'] not in to        # HR / Finance sign inside the system
    it_mail = next(m for m in app.config['MAIL_OUTBOX'] if m['to'] == ['it@a.test'])
    assert it_token in it_mail['html'] and 'حاسوب' in it_mail['html']   # the open custody is listed
    # duplicate open form for the same employee needs confirmation
    r = hr.post('/clearance/new', data={'employee_id': emp_id, 'departments': [t['dept_a']]})
    assert r.status_code == 400

    anon = app.test_client()
    page = anon.get(f'/sign/{it_token}').get_data(as_text=True)
    assert 'منى الموظفة' in page and 'حاسوب' in page
    assert anon.get('/sign/not-a-token').status_code == 404
    r = anon.post(f'/sign/{it_token}', data={'status': 'held', 'note': '', 'by_name': 'مسؤول التقنية'},
                  follow_redirects=True)
    assert 'اكتب سبب التعليق' in r.get_data(as_text=True)
    anon.post(f'/sign/{it_token}', data={'status': 'held', 'note': 'إرجاع الحاسوب', 'by_name': 'مسؤول التقنية'})
    anon.post(f'/sign/{lib_token}', data={'status': 'cleared', 'note': '', 'by_name': 'أمين المكتبة'})
    # the department manager of IT (mgr) cannot sign HR's row
    mgr = _client(app, 'mgr')
    assert mgr.post(f'/clearance/respond/{hr_resp}', data={'status': 'cleared'}).status_code == 403
    assert 'منى الموظفة' in mgr.get('/departments/portal').get_data(as_text=True)
    hr.post(f'/clearance/respond/{hr_resp}', data={'status': 'cleared', 'by_name': 'سارة'})
    app.config['MAIL_OUTBOX'] = []
    fin = _client(app, 'fin1')
    fin.post(f'/clearance/respond/{fin_resp}', data={'status': 'cleared', 'by_name': 'خالد'})
    with tenant_context(t['a']):
        form = db.session.get(ClearanceForm, form_id)
        assert form.status == 'completed' and [r.department_id for r in form.held] == [t['dept_a']]
    done = app.config['MAIL_OUTBOX']
    assert any(m['to'] == ['mona@a.test'] and 'إرجاع الحاسوب' in m['html'] for m in done)
    assert any(set(m['to']) == {'hr@a.test', 'fin@a.test'} for m in done)

    # HR → finance note, finance settles, the form is closed and signing is refused afterwards
    hr.post(f'/clearance/{form_id}/action', data={'action': 'to_finance', 'note': 'خصم قيمة الحاسوب'})
    assert 'منى الموظفة' in fin.get('/finance').get_data(as_text=True)
    fin.post(f'/finance/{form_id}/settle', data={'note': 'خُصمت 300'})
    with tenant_context(t['a']):
        form = db.session.get(ClearanceForm, form_id)
        assert form.status == 'closed' and form.settled and form.finance_note == 'خُصمت 300'
    r = anon.post(f'/sign/{it_token}', data={'status': 'cleared', 'by_name': 'x'}, follow_redirects=True)
    assert 'مقفل' in r.get_data(as_text=True)
    printed = hr.get(f'/clearance/{form_id}/print').get_data(as_text=True)
    assert 'إثبات تسوية مالية' in printed and 'خُصمت 300' in printed and 'نموذج ر ع ع - 1 2026' in printed
    # other tenant's admin cannot open it
    other = app.test_client()
    login(other, 'admin', org='b')
    assert other.get(f'/clearance/{form_id}').status_code == 404


def test_clearance_bulk_and_reminders(app, two_tenants):
    t = _setup(two_tenants)
    hr = _client(app, 'hr1')
    for name in ('أ', 'ب'):
        hr.post('/clearance/new', data={'employee_name': f'موظف {name}', 'departments': [t['dept_a'], t['dept_a2']]})
    with tenant_context(t['a']):
        f1, f2 = ClearanceForm.query.order_by(ClearanceForm.id).all()
        f1.created_at = utcnow() - timedelta(days=4)
        db.session.commit()
        from services.clearance import due_reminders
        assert [f.id for f in due_reminders()] == [f1.id]
        ids = [f1.id, f2.id]
    app.config['MAIL_OUTBOX'] = []
    hr.post('/clearance/bulk', data={'action': 'remind', 'ids': ids})
    assert sum('تذكير' in m['subject'] for m in app.config['MAIL_OUTBOX']) == 4
    hr.post('/clearance/bulk', data={'action': 'delete', 'ids': [ids[0]]})     # nobody signed → may be deleted
    with tenant_context(t['a']):
        assert db.session.get(ClearanceForm, ids[0]) is None
        assert ClearanceResponse.query.count() == 2


def test_custody_scoping_return_and_import(app, two_tenants):
    t = _setup(two_tenants)
    mgr = _client(app, 'mgr')                    # manager of dept_a only
    r = mgr.post('/custody/new', data={'item': 'مفتاح المختبر', 'recipient_name': 'علي', 'recipient_email': 'ali@a.test',
                                       'department_id': t['dept_a2']}, follow_redirects=True)
    assert 'اختر قسمًا من أقسامك' in r.get_data(as_text=True)
    app.config['MAIL_OUTBOX'] = []
    mgr.post('/custody/new', data={'item': 'مفتاح المختبر', 'recipient_name': 'علي', 'recipient_email': 'ali@a.test',
                                   'department_id': t['dept_a']})
    assert any(m['to'] == ['ali@a.test'] for m in app.config['MAIL_OUTBOX'])
    with tenant_context(t['a']):
        cid = Custody.query.one().id
    mgr.post('/custody/bulk', data={'action': 'return', 'ids': [cid], 'returned_to': 'مسؤول التقنية'})
    with tenant_context(t['a']):
        c = db.session.get(Custody, cid)
        assert c.status == 'returned' and c.returned_to == 'مسؤول التقنية'
    assert 'مفتاح المختبر' in mgr.get(f'/custody/receipt?ids={cid}').get_data(as_text=True)
    text = 'العهدة,المستلم,القسم,الحالة\nكتاب,ريم,المكتبة أ,مسلمة\nلابتوب,سامي,تقنية المعلومات أ,مسترجعة\n'
    r = mgr.post('/custody/import', data={'text': text}, follow_redirects=True)
    assert 'أُضيف 1' in r.get_data(as_text=True)          # the library row is not his department
    admin = _client(app, 'admin')
    admin.post('/custody/import', data={'text': text})
    with tenant_context(t['a']):
        assert Custody.query.filter_by(item='كتاب').one().status == 'handed'
        assert Custody.query.filter_by(item='لابتوب').count() == 2
    # a department that has custody cannot be deleted
    admin.post('/admin/departments/bulk', data={'action': 'delete', 'ids': [t['dept_a']]})
    with tenant_context(t['a']):
        assert db.session.get(Department, t['dept_a']) is not None


def test_dashboard_numbers_link_to_lists(app, two_tenants):
    _setup(two_tenants)
    hr = _client(app, 'hr1')
    page = hr.get('/home').get_data(as_text=True)
    assert 'براءة الذمة' in page and 'قيد التنفيذ' in page and '/clearance/?f=' in page
    assert hr.get('/clearance/').status_code == 200
    assert hr.get('/employees/').status_code == 200
    assert hr.get('/custody/').status_code == 200
    assert hr.get('/my').status_code == 200
