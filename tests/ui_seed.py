"""بيانات تجربة لمراجعة المظهر (tests/ui_audit.py): منظمة، وأقسام، ومستخدمون، وموظفون، وعهد، ونماذج براءة ذمة.

    python tests/ui_seed.py <ملف القاعدة> [ar|en]
اللغة الثانية لغة البيانات (الأسماء والأقسام والعهد)، لفحص الواجهة الإنجليزية ببيانات إنجليزية.
يطبع معرّفات JSON يستخدمها ui_audit.py.
"""
import json
import os
import sys

DB = sys.argv[1]
EN = len(sys.argv) > 2 and sys.argv[2] == 'en'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['TEST_DATABASE_URL'] = 'sqlite:///' + DB.replace('\\', '/')
os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
os.environ.setdefault('SECRET_KEY', 'ui-audit-secret')
os.environ.setdefault('BASE_URL', 'http://127.0.0.1:5096')
os.environ['DISABLE_REMINDERS'] = '1'

try:
    import pytest  # noqa: F401  — tests.conftest يستورده؛ قد لا يكون مثبّتاً على كمبيوتر المدرسة
except ImportError:
    import types
    sys.modules['pytest'] = types.SimpleNamespace(
        fixture=lambda *a, **k: a[0] if a and callable(a[0]) else (lambda f: f))

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import ClearanceForm, Custody, Department, Employee, SuperAdmin, Tenant  # noqa: E402
from security import hash_password  # noqa: E402
from tenancy import tenant_context  # noqa: E402
from tests.conftest import PASSWORD, login, make_dept, make_tenant, make_user  # noqa: E402

app = create_app({'MAIL_BACKEND': 'memory', 'WTF_CSRF_ENABLED': False, 'SESSION_COOKIE_SECURE': False})
with app.app_context():
    from flask_migrate import upgrade
    upgrade(directory=os.path.join(ROOT, 'migrations'))
    L = (lambda ar, en: en if EN else ar)
    t = make_tenant('raed', L('مدرسة تجريبية', 'Trial School'))
    if EN:
        t.name_en = 'Trial School'
        t.form_footer = 'Form CL-1 2026'
    it = make_dept(t, 'IT', L('تقنية المعلومات', 'Information Technology'), emails='it@raed.test')
    lib = make_dept(t, 'LIB', L('المكتبة', 'Library'), emails='lib@raed.test')
    hr = make_dept(t, 'HR', L('شؤون الموظفين', 'Human Resources'), 'HR', emails='hr@raed.test')
    fin = make_dept(t, 'FIN', L('المحاسبة', 'Accounting'), 'Finance', emails='fin@raed.test')
    make_user(t, 'admin', ('admin',), email='admin@raed.test')
    make_user(t, 'hr1', ('hr',), (hr,), email='hr1@raed.test')
    make_user(t, 'mgr', ('dept_manager',), (it,), email='mgr@raed.test')
    if EN:
        from models import User
        with tenant_context(t.id):
            for u in User.query.all():
                u.full_name = f'User {u.username}'
            db.session.commit()
    db.session.commit()
    db.session.add(SuperAdmin(username='root', password_hash=hash_password(PASSWORD)))
    db.session.commit()
    with tenant_context(t.id):
        emps = []
        for i, (name, dept) in enumerate((
                [('Mona Khaled', it), ('Sami Abdullah', lib), ('Reem Hasan', it), ('Khaled Yousef', hr),
                 ('Hiba Ali', lib), ('Omar Saleem', it)] if EN else
                [('منى خالد', it), ('سامي عبد الله', lib), ('ريم حسن', it), ('خالد يوسف', hr),
                 ('هبة علي', lib), ('عمر سليم', it)])):
            e = Employee(full_name=name, employee_number=str(100 + i), email=None if i == 5 else f'e{i}@raed.test',
                         department_id=dept, job_title=L('معلم', 'Teacher') if i % 2 else L('إداري', 'Administrator'))
            db.session.add(e)
            emps.append(e)
        db.session.commit()
        for i, (item, emp) in enumerate([(L('حاسوب محمول', 'Laptop'), emps[0]), (L('مفتاح المختبر', 'Lab key'), emps[0]),
                                         (L('كتاب مرجعي', 'Reference book'), emps[1]), (L('جهاز عرض', 'Projector'), emps[2])]):
            db.session.add(Custody(ref_code=f'CUST-UI{i}', item=item, recipient_name=emp.full_name, employee_id=emp.id,
                                   department_id=emp.department_id, status='handed' if i != 3 else 'returned'))
        db.session.commit()
        emp_ids = [e.id for e in emps]
    c = app.test_client()
    login(c, 'hr1', org='raed')
    for eid in emp_ids[:3]:
        c.post('/clearance/new', data={'employee_id': eid, 'resign_date': '2026-11-30', 'departments': [it, lib, hr, fin]})
    with tenant_context(t.id):
        forms = ClearanceForm.query.order_by(ClearanceForm.id).all()
        tok = [r.token for r in forms[0].responses if r.department_id == lib][0]
        form_ids = [f.id for f in forms]
    anon = app.test_client()
    anon.post(f'/sign/{tok}', data={'status': 'cleared', 'note': '', 'by_name': L('أمين المكتبة', 'Librarian')})
    with tenant_context(t.id):
        it_tok = [r.token for r in db.session.get(ClearanceForm, form_ids[1]).responses if r.department_id == it][0]
        cid = Custody.query.first().id
    anon.post(f'/sign/{it_tok}', data={'status': 'held', 'note': L('إرجاع الحاسوب', 'Return the laptop'), 'by_name': L('مسؤول التقنية', 'IT officer')})
    print(json.dumps({'tenant': t.id, 'form': form_ids[0], 'emp': emp_ids[0], 'dept': it, 'custody': cid, 'sign': tok,
                      'role': 1, 'user': 1}))
