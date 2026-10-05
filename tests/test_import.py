import csv

import pytest

from extensions import db
from models import Department, ClearanceForm, ClearanceResponse, Custody, Tenant
from tenancy import tenant_context
from tests.conftest import make_tenant, make_user, login
from utils.dateparse import parse_sheet_date

RLM = '‏'
HIJRI = f'١٣{RLM}/٤{RLM}/١٤٤٨ هـ'          # what toLocaleDateString('ar-SA') writes → 2026-09-24


def write(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        csv.writer(fh).writerows(rows)


@pytest.fixture
def sheets(tmp_path):
    write(tmp_path / 'الاقسام.csv', [
        ['id', 'code', 'name', 'icon', 'manager', 'email', 'type'],
        ['D1', 'IT55', 'تقنية المعلومات', '💻', 'فادي', 'it@raed.test', 'normal'],
        ['D2', 'LB77', 'المكتبة', '📚', 'هبة', 'lib1@raed.test; lib2@raed.test', ''],
        ['D3', 'HR11', 'شؤون الموظفين', '👥', 'منى', 'hr@raed.test', 'HR'],
        ['D4', 'FN22', 'المحاسبة', '💰', 'رامي', 'fin@raed.test', 'Finance'],
        ['', 'X', '', '', '', '', ''],                                      # ignored like getDepartments()
        ['D5', 'LB99', 'المختبر', '🧪', 'سعاد', 'not-an-email', 'normal'],
    ])
    write(tmp_path / 'النماذج.csv', [
        ['formId', 'formCode', 'empCode', 'employeeName', 'employeeId', 'employeeEmail',
         'department', 'resignDate', 'createdAt', 'status', 'hrNote'],
        ['FORM-AAA111', 'CLR-2026-AAA111', 'EMP-1', 'خالد حسن', '501', 'khaled@raed.test',
         'الرياضيات', '30/09/2026', HIJRI, 'مقفل', 'تسوية محاسبة: خُصم ثمن الكتاب'],
        ['FORM-BBB222', 'CLR-2026-BBB222', 'EMP-2', 'ليلى', '502', '', 'العلوم', '2026-10-01',
         '١/٥/١٤٤٨ هـ', 'قيد التنفيذ', 'سلفة'],
        ['FORM-CCC333', 'CLR-2026-CCC333', 'EMP-3', 'عمر', '503', '', '', '', 'كلام', 'حالة غريبة', ''],
        ['', '', '', '', '', '', '', '', '', '', ''],
    ])
    write(tmp_path / 'الردود.csv', [
        ['formId', 'deptId', 'status', 'note', 'respondedAt', 'respondedBy'],
        ['FORM-AAA111', 'D1', 'خالية الذمة', '', HIJRI, 'فادي'],
        ['FORM-AAA111', 'D2', 'معلق', 'كتاب مفقود', HIJRI, 'هبة'],
        ['FORM-AAA111', 'D2', 'خالية الذمة', '', '', ''],                 # duplicate
        ['FORM-AAA111', 'D3', 'خالية الذمة', '', '', 'منى'],
        ['FORM-AAA111', 'D9', 'خالية الذمة', '', '', 'قديم'],            # deleted department
        ['FORM-BBB222', 'D1', 'قيد الانتظار', '', '', ''],
        ['FORM-ZZZ', 'D1', 'معلق', '', '', ''],                         # unknown form
    ])
    write(tmp_path / 'نظام براءة الذمة - العهد.csv', [
        ['معرف_العهدة', 'العهدة', 'المستلم', 'بريد_المستلم', 'القسم', 'كود_القسم', 'مسلم_العهدة',
         'تاريخ_التسليم', 'الحالة', 'تاريخ_الإرجاع', 'مستلم_الإرجاع', 'بريد_مسلم_العهدة'],
        ['CUST-AB12CD', 'آيباد', 'خالد حسن', 'khaled@raed.test', 'تقنية المعلومات', 'IT55', 'فادي',
         HIJRI, 'مسترجعة', '١/٥/١٤٤٨ هـ', 'سامر', 'fadi@raed.test', 'samer@raed.test'],
        ['CUST-EF34GH', 'مفتاح المختبر', 'ليلى', '', 'قسم قديم', 'OLD1', 'سعاد', '٢٠٢٦/٠٩/٠١',
         'مسلمة', '', '', ''],
        ['CUST-IJ56KL', 'كتاب', 'عمر', '', 'المكتبة', 'LB77', 'هبة', 'أمس', 'مفقودة', '', '', ''],
    ])
    return tmp_path


@pytest.fixture
def raed(ctx):
    t = make_tenant('raed', 'مدرسة الرائد العربي')
    make_user(t, 'admin', ('admin',), email='admin@raed.test')
    make_user(t, 'khaled', ('employee',), email='khaled@raed.test')
    return t.id


def run(app, folder, *extra):
    return app.test_cli_runner().invoke(args=['import-sheets', 'raed', '--dir', str(folder), *extra])


def counts(tid):
    with tenant_context(tid):
        return (Department.query.count(), ClearanceForm.query.count(),
                ClearanceResponse.query.count(), Custody.query.count())


def test_date_parser():
    assert str(parse_sheet_date(HIJRI).value) == '2026-09-24'
    assert str(parse_sheet_date('٢٠٢٦/٠٩/٠١').value) == '2026-09-01'
    assert str(parse_sheet_date('30/09/2026').value) == '2026-09-30'
    assert str(parse_sheet_date('9/30/2026', 'mdy').value) == '2026-09-30'
    assert parse_sheet_date('3/4/2026').warning                       # ambiguous is reported
    assert parse_sheet_date('كلام').value is None


def test_dry_run_writes_nothing(app, raed, sheets):
    r = run(app, sheets)
    assert r.exit_code == 0, r.output
    assert 'تشغيل تجريبي' in r.output and 'نماذج جديدة: 3' in r.output
    assert counts(raed) == (0, 0, 0, 0)


def test_import_commit_and_mapping(app, raed, sheets):
    r = run(app, sheets, '--commit')
    assert r.exit_code == 0, r.output
    assert counts(raed) == (5, 3, 5, 3)
    with tenant_context(raed):
        it = Department.query.filter_by(code='IT55').one()
        assert it.legacy_id == 'D1' and it.icon == '💻' and it.manager_name == 'فادي'
        assert Department.query.filter_by(code='LB77').one().emails == 'lib1@raed.test, lib2@raed.test'
        assert Department.query.filter_by(code='HR11').one().dept_type == 'HR'
        assert Department.query.filter_by(code='LB99').one().emails is None

        a = ClearanceForm.query.filter_by(code='CLR-2026-AAA111').one()
        assert a.status == 'closed' and a.is_settled
        assert a.finance_note == 'خُصم ثمن الكتاب' and a.hr_note is None   # split, not overwritten
        assert str(a.resign_date) == '2026-09-30' and a.created_at_raw == HIJRI
        assert a.created_at.date().isoformat() == '2026-09-24'           # Hijri converted
        assert a.employee_user_id is not None                            # matched by email
        names = [r_.name for r_ in a.responses]
        assert names == ['تقنية المعلومات', 'المكتبة', 'شؤون الموظفين', 'محذوف']
        lib_resp = a.responses[1]
        assert lib_resp.status == 'held' and lib_resp.note == 'كتاب مفقود' and lib_resp.responded_by == 'هبة'

        b = ClearanceForm.query.filter_by(code='CLR-2026-BBB222').one()
        assert b.hr_note == 'سلفة' and b.status == 'in_progress'
        c = ClearanceForm.query.filter_by(code='CLR-2026-CCC333').one()
        assert c.status == 'in_progress' and c.created_at_raw == 'كلام'

        ipad = Custody.query.filter_by(code='CUST-AB12CD').one()
        assert ipad.status == 'returned' and ipad.returned_to_name == 'سامر'
        assert ipad.returned_to_email == 'samer@raed.test'                 # 13th column
        assert ipad.handed_by_email == 'fadi@raed.test'
        assert ipad.handed_at.date().isoformat() == '2026-09-24' and ipad.handed_at_raw == HIJRI
        assert ipad.department_id == it.id and ipad.source == 'sheet' and ipad.source_row == 2
        key = Custody.query.filter_by(code='CUST-EF34GH').one()
        assert key.department_id is None and key.department_name == 'قسم قديم'
        assert Custody.query.filter_by(code='CUST-IJ56KL').one().status == 'handed'

    out = r.output
    for expected in ('رد مكرر', 'قسم محذوف (D9)', 'نموذج غير موجود FORM-ZZZ',
                     'حالة غير معروفة "حالة غريبة"', 'حالة غير معروفة "مفقودة"',
                     'قسم غير معروف (كود OLD1', 'بريد غير صالح: not-an-email',
                     'صيغة تاريخ غير معروفة: أمس'):
        assert expected in out, expected


def test_import_is_idempotent(app, raed, sheets):
    run(app, sheets, '--commit')
    before = counts(raed)
    r = run(app, sheets, '--commit')
    assert r.exit_code == 0 and counts(raed) == before
    assert 'نماذج موجودة مسبقًا: 3' in r.output and 'عهد موجودة مسبقًا: 3' in r.output


def test_imported_data_works_in_the_app(app, raed, sheets):
    run(app, sheets, '--commit')
    c = app.test_client()
    assert login(c, 'admin').status_code == 302
    with tenant_context(raed):
        fid = ClearanceForm.query.filter_by(code='CLR-2026-AAA111').one().id
    html = c.get(f'/clearance/{fid}/print').get_data(as_text=True)
    assert 'إثبات تسوية مالية' in html and 'خُصم ثمن الكتاب' in html and 'محذوف' in html
    assert 'آيباد' in c.get('/custody/').get_data(as_text=True)
    k = app.test_client()
    login(k, 'khaled')
    assert 'آيباد' in k.get('/custody/mine').get_data(as_text=True)
    assert 'CLR-2026-AAA111' in k.get('/clearance/mine').get_data(as_text=True)


def test_missing_departments_file_fails(app, raed, tmp_path):
    write(tmp_path / 'النماذج.csv', [['h']])
    r = run(app, tmp_path)
    assert r.exit_code != 0
