"""Import the four sheets of the Apps Script spreadsheet from CSV exports.

    flask import-sheets <org-slug> --dir <folder>            # dry run: report only
    flask import-sheets <org-slug> --dir <folder> --commit   # write

Columns are read by POSITION, exactly as the script read them, so renamed
headers do not matter:

  الاقسام : id, code, name, icon, manager, email, type
  النماذج : formId, formCode, empCode, employeeName, employeeId, employeeEmail,
            department, resignDate, createdAt, status, hrNote
  الردود  : formId, deptId, status, note, respondedAt, respondedBy
  العهد   : معرف_العهدة, العهدة, المستلم, بريد_المستلم, القسم, كود_القسم, مسلم_العهدة,
            تاريخ_التسليم, الحالة, تاريخ_الإرجاع, مستلم_الإرجاع, بريد_مسلم_العهدة,
            [13th, unnamed: بريد مستلم الإرجاع written by returnCustody()]

Re-running is safe: rows whose reference (department code, form code,
custody id) already exists are skipped, so nothing is duplicated or lost.
Original date text is kept in the *_raw columns next to the parsed value.
"""
import csv
import io
import os
from collections import Counter

from extensions import db
from models import (Department, ClearanceForm, ClearanceResponse, Custody, User)
from models.clearance import FORM_STATUS_FROM_AR, RESPONSE_STATUS_FROM_AR
from models.custody import CUSTODY_STATUS_FROM_AR
from utils.audit import audit
from utils.dateparse import parse_sheet_date, date_to_utc_noon, normalize
from utils.helpers import split_emails

SHEETS = {
    'departments': ('الاقسام', 'الأقسام', 'departments'),
    'forms': ('النماذج', 'forms'),
    'responses': ('الردود', 'responses'),
    'custody': ('العهد', 'custody', 'custodies'),
}
DEPT_TYPES = {'normal': 'normal', 'hr': 'HR', 'finance': 'Finance', '': 'normal'}
SETTLEMENT_PREFIX = 'تسوية محاسبة:'


def find_sheet_files(folder):
    """Match exported files by sheet name: 'X.csv' or Google's
    'Spreadsheet name - X.csv'."""
    found = {}
    files = [f for f in os.listdir(folder) if f.lower().endswith('.csv')]
    for key, names in SHEETS.items():
        for f in files:
            stem = os.path.splitext(f)[0].strip()
            tail = stem.rsplit(' - ', 1)[-1].strip()
            if tail in names or stem in names:
                found[key] = os.path.join(folder, f)
                break
    return found


def read_rows(path):
    with open(path, 'r', encoding='utf-8-sig', newline='') as fh:
        text = fh.read()
    rows = list(csv.reader(io.StringIO(text)))
    return rows[1:] if rows else []          # first row is the header, as in the script


def cell(row, i):
    return row[i].strip() if i < len(row) and row[i] is not None else ''


class Report:
    def __init__(self):
        self.counts = Counter()
        self.warnings = []

    def warn(self, sheet, row_no, msg):
        self.warnings.append(f'[{sheet} صف {row_no}] {msg}')

    def text(self):
        lines = ['== تقرير الاستيراد ==']
        for k, v in sorted(self.counts.items()):
            lines.append(f'{k}: {v}')
        lines.append(f'تحذيرات: {len(self.warnings)}')
        lines += ['  ' + w for w in self.warnings]
        return '\n'.join(lines)


class SheetImporter:
    def __init__(self, tenant, files, date_order='dmy', tz='Asia/Amman'):
        self.tenant, self.files, self.order, self.tz = tenant, files, date_order, tz
        self.report = Report()
        self.dept_by_legacy, self.dept_by_code, self.form_by_legacy = {}, {}, {}

    # ── helpers ──
    def _date(self, sheet, row_no, text, label):
        if not text:
            return None, None
        p = parse_sheet_date(text, self.order)
        if p.warning:
            self.report.warn(sheet, row_no, f'{label}: {p.warning} ({normalize(text)})')
        if p.kind == 'hijri':
            self.report.counts['تواريخ هجرية حُوّلت'] += 1
        return p.value, text

    def run(self):
        missing = [k for k in ('departments',) if k not in self.files]
        if missing:
            raise ValueError('ملف الأقسام مطلوب (الاقسام.csv)')
        self.import_departments()
        if 'forms' in self.files:
            self.import_forms()
        if 'responses' in self.files:
            self.import_responses()
        if 'custody' in self.files:
            self.import_custody()
        audit('import.sheets', 'tenant', self.tenant.id, actor='استيراد Google Sheets',
              counts=dict(self.report.counts), warnings=len(self.report.warnings))
        return self.report

    # ── الاقسام ──
    def import_departments(self):
        for d in Department.query.all():
            self.dept_by_code[d.code] = d
            if d.legacy_id:
                self.dept_by_legacy[d.legacy_id] = d
        for i, row in enumerate(read_rows(self.files['departments']), start=2):
            legacy, code, name = cell(row, 0), cell(row, 1), cell(row, 2)
            if not legacy or not name:            # same filter as getDepartments()
                if any(c.strip() for c in row):
                    self.report.warn('الاقسام', i, 'صف بلا معرّف أو اسم — تم تجاهله')
                continue
            code = code or legacy
            dtype = DEPT_TYPES.get(cell(row, 6).lower())
            if dtype is None:
                self.report.warn('الاقسام', i, f'نوع غير معروف "{cell(row, 6)}" — اعتُبر قسمًا عاديًا')
                dtype = 'normal'
            emails = split_emails(cell(row, 5))
            if cell(row, 5) and not emails:
                self.report.warn('الاقسام', i, f'بريد غير صالح: {cell(row, 5)}')
            existing = self.dept_by_code.get(code)
            if existing:
                if not existing.legacy_id:
                    existing.legacy_id = legacy
                self.dept_by_legacy[legacy] = existing
                self.report.counts['أقسام موجودة مسبقًا'] += 1
                continue
            d = Department(legacy_id=legacy, code=code, name=name, icon=cell(row, 3) or '🏢',
                           manager_name=cell(row, 4) or None, emails=', '.join(emails) or None,
                           dept_type=dtype, sort_order=i, is_active=True)
            db.session.add(d)
            self.dept_by_code[code] = d
            self.dept_by_legacy[legacy] = d
            self.report.counts['أقسام جديدة'] += 1
        db.session.flush()
        for dtype in ('HR', 'Finance'):
            active = [d for d in self.dept_by_code.values() if d.dept_type == dtype and d.is_active]
            if len(active) > 1:
                for extra in active[1:]:
                    extra.is_active = False
                self.report.warn('الاقسام', 0, f'أكثر من قسم {dtype}؛ بقي الأول فعّالًا: {active[0].name}')

    # ── النماذج ──
    def import_forms(self):
        existing = {f.code: f for f in ClearanceForm.query.all()}
        users_by_email = {u.email.lower(): u.id for u in User.query.all() if u.email}
        for i, row in enumerate(read_rows(self.files['forms']), start=2):
            legacy, code = cell(row, 0), cell(row, 1)
            if not legacy:                         # getForms() skips rows without an id
                continue
            code = code or legacy
            if code in existing:
                self.form_by_legacy[legacy] = existing[code]
                self.report.counts['نماذج موجودة مسبقًا'] += 1
                continue
            status_ar = cell(row, 9) or 'قيد التنفيذ'
            status = FORM_STATUS_FROM_AR.get(status_ar)
            if status is None:
                self.report.warn('النماذج', i, f'حالة غير معروفة "{status_ar}" — اعتُبرت قيد التنفيذ')
                status = 'in_progress'
            resign, resign_raw = self._date('النماذج', i, cell(row, 7), 'تاريخ الاستقالة')
            created, created_raw = self._date('النماذج', i, cell(row, 8), 'تاريخ الإنشاء')
            note = cell(row, 10)
            hr_note, fin_note = (None, note[len(SETTLEMENT_PREFIX):].strip()) \
                if note.startswith(SETTLEMENT_PREFIX) else (note or None, None)
            email = cell(row, 5) or None
            f = ClearanceForm(
                legacy_id=legacy, code=code, emp_code=cell(row, 2) or None,
                employee_name=cell(row, 3) or '(بدون اسم)', employee_number=cell(row, 4) or None,
                employee_email=email, employee_department=cell(row, 6) or None,
                employee_user_id=users_by_email.get(email.lower()) if email else None,
                resign_date=resign, resign_date_raw=resign_raw,
                status=status, hr_note=hr_note, finance_note=fin_note,
                created_at=date_to_utc_noon(created, self.tz) if created else
                date_to_utc_noon(resign, self.tz) if resign else None,
                created_at_raw=created_raw)
            if f.created_at is None:
                from models.core import utcnow
                f.created_at = utcnow()
                self.report.warn('النماذج', i, 'لا يوجد تاريخ إنشاء صالح — استُخدم تاريخ اليوم (النص الأصلي محفوظ)')
            if not cell(row, 3):
                self.report.warn('النماذج', i, 'نموذج بلا اسم موظف')
            db.session.add(f)
            existing[code] = f
            self.form_by_legacy[legacy] = f
            self.report.counts['نماذج جديدة'] += 1
        db.session.flush()

    # ── الردود ──
    def import_responses(self):
        seen = {(r.form_id, r.department_id) for r in ClearanceResponse.query.all()}
        order = Counter()
        new_forms = {f.id for f in self.form_by_legacy.values() if not ClearanceResponse.query.filter_by(form_id=f.id).first()}
        for i, row in enumerate(read_rows(self.files['responses']), start=2):
            form_legacy, dept_legacy = cell(row, 0), cell(row, 1)
            if not form_legacy:
                continue
            form = self.form_by_legacy.get(form_legacy)
            if form is None:
                self.report.warn('الردود', i, f'رد لنموذج غير موجود {form_legacy} — تم تجاهله')
                continue
            if form.id not in new_forms:
                self.report.counts['ردود موجودة مسبقًا'] += 1
                continue
            dept = self.dept_by_legacy.get(dept_legacy)
            key = (form.id, dept.id if dept else f'missing:{dept_legacy}')
            if key in seen:
                self.report.warn('الردود', i, 'رد مكرر لنفس القسم والنموذج — أُخذ الأول')
                continue
            seen.add(key)
            status_ar = cell(row, 2) or 'قيد الانتظار'
            status = RESPONSE_STATUS_FROM_AR.get(status_ar)
            if status is None:
                self.report.warn('الردود', i, f'حالة غير معروفة "{status_ar}" — اعتُبرت قيد الانتظار')
                status = 'pending'
            if dept is None:
                self.report.warn('الردود', i, f'قسم محذوف ({dept_legacy}) — حُفظ الرد باسم "محذوف"')
            when, when_raw = self._date('الردود', i, cell(row, 4), 'تاريخ الرد')
            db.session.add(ClearanceResponse(
                form_id=form.id, department_id=dept.id if dept else None,
                department_name=dept.name if dept else 'محذوف',
                department_icon=dept.icon if dept else '❓', sort_order=order[form.id],
                status=status, note=cell(row, 3) or None,
                responded_at=date_to_utc_noon(when, self.tz) if when else None,
                responded_at_raw=when_raw, responded_by=cell(row, 5) or None))
            order[form.id] += 1
            self.report.counts['ردود جديدة'] += 1
        db.session.flush()

    # ── العهد ──
    def import_custody(self):
        existing = {c.code for c in Custody.query.all()}
        users_by_email = {u.email.lower(): u.id for u in User.query.all() if u.email}
        dept_by_name = {d.name: d for d in self.dept_by_code.values()}
        for i, row in enumerate(read_rows(self.files['custody']), start=2):
            code = cell(row, 0)
            if not code:
                if any(c.strip() for c in row):
                    self.report.warn('العهد', i, 'صف بلا معرّف عهدة — تم تجاهله')
                continue
            if code in existing:
                self.report.counts['عهد موجودة مسبقًا'] += 1
                continue
            if not cell(row, 1):
                self.report.warn('العهد', i, 'عهدة بلا وصف — حُفظت بوصف "(غير محدد)"')
            dept = self.dept_by_code.get(cell(row, 5)) or dept_by_name.get(cell(row, 4))
            if dept is None:
                self.report.warn('العهد', i, f'قسم غير معروف (كود {cell(row, 5)}، اسم {cell(row, 4)}) — حُفظت باسم القسم كنص')
            status_ar = cell(row, 8) or 'مسلمة'
            status = CUSTODY_STATUS_FROM_AR.get(status_ar)
            if status is None:
                self.report.warn('العهد', i, f'حالة غير معروفة "{status_ar}" — اعتُبرت مسلمة')
                status = 'handed'
            handed, handed_raw = self._date('العهد', i, cell(row, 7), 'تاريخ التسليم')
            returned, returned_raw = self._date('العهد', i, cell(row, 9), 'تاريخ الإرجاع')
            r_email = (split_emails(cell(row, 3)) or [None])[0]
            if cell(row, 3) and not r_email:
                self.report.warn('العهد', i, f'بريد مستلم غير صالح: {cell(row, 3)}')
            db.session.add(Custody(
                code=code, item_name=cell(row, 1) or '(غير محدد)',
                recipient_name=cell(row, 2) or '(غير محدد)', recipient_email=r_email,
                recipient_user_id=users_by_email.get(r_email.lower()) if r_email else None,
                department_id=dept.id if dept else None,
                department_name=dept.name if dept else (cell(row, 4) or None),
                department_code=cell(row, 5) or None, handed_by_name=cell(row, 6) or None,
                handed_by_email=(split_emails(cell(row, 11)) or [None])[0],
                handed_at=date_to_utc_noon(handed, self.tz) if handed else None,
                handed_at_raw=handed_raw, status=status,
                returned_at=date_to_utc_noon(returned, self.tz) if returned else None,
                returned_at_raw=returned_raw, returned_to_name=cell(row, 10) or None,
                returned_to_email=(split_emails(cell(row, 12)) or [None])[0],
                source='sheet', source_row=i))
            existing.add(code)
            self.report.counts['عهد جديدة'] += 1
        db.session.flush()


DRIVE_LOGOS = ('1m7e1pkmfnAS3Q9cmN79Sfzw-X3a_KiWx', '1ip2QRYokf8zZgiakTKF_FWu3ARjlei5c')


def fetch_drive_logos(tenant, upload_dir):
    """Download the two logos the print pages used (Google Drive thumbnails)
    into the organization's settings. Returns a list of messages."""
    import requests
    msgs = []
    folder = os.path.join(upload_dir, 'tenants', str(tenant.id))
    os.makedirs(folder, exist_ok=True)
    for which, file_id in zip(('primary', 'secondary'), DRIVE_LOGOS):
        url = f'https://drive.google.com/thumbnail?id={file_id}&sz=w400'
        try:
            r = requests.get(url, timeout=20)
            head = r.content[:8]
            ext = '.png' if head.startswith(b'\x89PNG') else '.jpg' if head.startswith(b'\xff\xd8\xff') else None
            if r.status_code != 200 or not ext:
                msgs.append(f'{which}: تعذر التنزيل (HTTP {r.status_code})')
                continue
            name = f'logo_{which}_drive{ext}'
            with open(os.path.join(folder, name), 'wb') as fh:
                fh.write(r.content)
            setattr(tenant, f'logo_{which}', name)
            msgs.append(f'{which}: تم')
        except Exception as e:
            msgs.append(f'{which}: {e}')
    return msgs
