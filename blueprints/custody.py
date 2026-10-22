"""Custody (العهد): hand an item to an employee from a department, return it, print the receipt, import a
list. Department managers see and manage only their departments; «custody.view_all / manage_all» see all;
every employee with a login sees his own items under «عهدي»."""
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort

from authz import permission_required, login_required, current_user
from extensions import db
from tenancy import get_scoped
from models import Custody, Department, Employee
from utils.audit import audit
from utils.helpers import short_code, split_emails, is_valid_email
from utils.mail import send_email
from utils.tabular import read_table, to_records, parse_date, norm

custody_bp = Blueprint('custody', __name__)
NOTICE = 'emails/notice.html'
ANY = ('custody.manage', 'custody.manage_all', 'custody.view_all')

FIELDS = [
    ('item', ['العهدة', 'الصنف', 'البند', 'item', 'itemName']),
    ('recipient', ['المستلم', 'اسم المستلم', 'الموظف', 'recipient', 'recipientName']),
    ('recipient_email', ['بريد المستلم', 'بريد_المستلم', 'البريد', 'email', 'recipientEmail']),
    ('department', ['القسم', 'كود القسم', 'كود_القسم', 'department', 'deptName', 'deptCode']),
    ('handed_by', ['مسلم العهدة', 'مسلم_العهدة', 'handedBy']),
    ('handed_at', ['تاريخ التسليم', 'تاريخ_التسليم', 'dateOut', 'date']),
    ('quantity', ['الكمية', 'العدد', 'quantity', 'qty']),
    ('serial_no', ['الرقم التسلسلي', 'الرقم المتسلسل', 'serial', 'serial_no']),
    ('status', ['الحالة', 'status']),
    ('returned_at', ['تاريخ الإرجاع', 'تاريخ_الإرجاع', 'dateIn']),
    ('returned_to', ['مستلم الإرجاع', 'مستلم_الإرجاع', 'receivedBy']),
    ('ref_code', ['معرف العهدة', 'معرف_العهدة', 'id']),
    ('handed_by_email', ['بريد مسلم العهدة', 'بريد_مسلم_العهدة', 'handedByEmail']),
]
FIELD_LABELS = ['العهدة', 'المستلم', 'بريد المستلم', 'القسم', 'مسلم العهدة', 'تاريخ التسليم', 'الكمية', 'الرقم التسلسلي']


def manageable_dept_ids(user):
    if user.has_perm('custody.manage_all'):
        return None            # all
    if user.has_perm('custody.manage') or user.has_perm('custody.import'):
        return set(user.department_ids)
    return set()


def visible_query(user):
    q = Custody.query
    if user.has_perm('custody.view_all') or user.has_perm('custody.manage_all'):
        return q
    return q.filter(Custody.department_id.in_(list(user.department_ids) or [0]))


def _can_manage(user, c):
    ids = manageable_dept_ids(user)
    return ids is None or c.department_id in ids


def _get(cid):
    c = get_scoped(Custody, cid)
    if c is None:
        abort(404)
    return c


def new_ref():
    while True:
        code = f'CUST-{short_code(6)}'
        if not Custody.query.filter_by(ref_code=code).first():
            return code


def _depts_for(user):
    ids = manageable_dept_ids(user)
    q = Department.query.filter_by(is_active=True)
    if ids is not None:
        q = q.filter(Department.id.in_(list(ids) or [0]))
    return q.order_by(Department.sort_order, Department.name).all()


def _ids():
    return {int(v) for v in request.form.getlist('ids') if v.isdigit()}


# ───────────────────────────── mails ─────────────────────────────
def mail_handed(c):
    if c.recipient_email:
        send_email([c.recipient_email], f'إشعار استلام عهدة: {c.item}', NOTICE, g.tenant, kind='custody.handed',
                   entity=('custody', c.id), heading='إشعار استلام عهدة', greeting=c.recipient_name,
                   paragraphs=[f'نود إعلامكم بتسليمكم العهدة التالية من قسم «{c.department.name}»:'],
                   code=c.item + (f' × {c.quantity}' if c.quantity > 1 else ''), code_label='تفاصيل العهدة',
                   facts=[('الرقم', c.ref_code)] + ([('الرقم التسلسلي', c.serial_no)] if c.serial_no else []),
                   note='يرجى المحافظة عليها وإرجاعها عند الانتهاء.', note_label='تنبيه')
    to = c.department.email_list + split_emails(c.handed_by_email)
    if to:
        send_email(to, f'إشعار تسليم عهدة: {c.recipient_name}', NOTICE, g.tenant, kind='custody.handed_admin',
                   entity=('custody', c.id), heading='إشعار تسليم عهدة',
                   paragraphs=[f'تم تسليم عهدة من قسم «{c.department.name}» للموظف «{c.recipient_name}».'],
                   facts=[('العهدة', c.item), ('الكمية', c.quantity), ('مسلم العهدة', c.handed_by or '—'),
                          ('الرقم', c.ref_code)],
                   after=['📌 سيتم إعلامكم آليًا فور إرجاع العهدة.'])


def mail_returned(c, received_email=None):
    to = split_emails(c.recipient_email) + c.department.email_list + split_emails(c.handed_by_email) + \
        split_emails(received_email)
    if to:
        send_email(to, f'إشعار استرجاع عهدة: {c.item}', NOTICE, g.tenant, kind='custody.returned',
                   entity=('custody', c.id), heading='إشعار استرجاع عهدة ✅',
                   paragraphs=['نود إعلامكم بأنه تم استرجاع العهدة التالية:'],
                   facts=[('العهدة', c.item), ('العهدة على الموظف', c.recipient_name),
                          ('مستلم الإرجاع بالقسم', c.returned_to or '—'), ('تاريخ الإرجاع', c.returned_at)])


# ───────────────────────────── list / add ─────────────────────────────
@custody_bp.route('/custody/')
@permission_required(*ANY)
def index():
    user = current_user()
    rows = visible_query(user).order_by(Custody.status, Custody.id.desc()).all()
    manage_ids = manageable_dept_ids(user)
    return render_template('custody/index.html', rows=rows, departments=_depts_for(user),
                           employees=Employee.query.filter_by(is_active=True).order_by(Employee.full_name).all(),
                           can_manage=lambda c: manage_ids is None or c.department_id in manage_ids,
                           may_add=manage_ids is None or bool(manage_ids), field_labels=FIELD_LABELS,
                           today=date.today().isoformat())


def _apply(c, f, user):
    errors = []
    item = (f.get('item') or '').strip()
    emp = get_scoped(Employee, int(f['employee_id'])) if (f.get('employee_id') or '').isdigit() else None
    name = (f.get('recipient_name') or '').strip() or (emp.full_name if emp else '')
    email = (f.get('recipient_email') or '').strip() or (emp.email if emp else '') or None
    did = f.get('department_id') or ''
    dept = get_scoped(Department, int(did)) if did.isdigit() else None
    if not item:
        errors.append('اكتب اسم العهدة')
    if not name:
        errors.append('اختر المستلم من سجل الموظفين أو اكتب اسمه')
    if email and not is_valid_email(email):
        errors.append('بريد المستلم غير صالح')
    ids = manageable_dept_ids(user)
    if dept is None or (ids is not None and dept.id not in ids):
        errors.append('اختر قسمًا من أقسامك')
    try:
        qty = max(int(f.get('quantity') or 1), 1)
    except ValueError:
        qty = 1
    if errors:
        return errors
    c.item, c.quantity, c.serial_no = item[:300], qty, (f.get('serial_no') or '').strip()[:100] or None
    c.employee_id, c.recipient_name, c.recipient_email = (emp.id if emp else None), name[:200], email
    c.department_id = dept.id
    c.handed_by = (f.get('handed_by') or '').strip()[:200] or user.full_name
    c.handed_by_email = (f.get('handed_by_email') or '').strip() or user.email
    c.handed_at = parse_date(f.get('handed_at')) or c.handed_at or date.today()
    c.notes = (f.get('notes') or '').strip()[:500] or None
    return []


@custody_bp.route('/custody/new', methods=['POST'])
@permission_required('custody.manage', 'custody.manage_all')
def add():
    user = current_user()
    c = Custody(ref_code=new_ref(), status='handed')
    errors = _apply(c, request.form, user)
    if errors:
        for e in errors:
            flash(e, 'danger')
        return redirect(url_for('custody.index'))
    db.session.add(c)
    db.session.flush()
    audit('custody.handed', 'custody', c.id, item=c.item, recipient=c.recipient_name)
    db.session.commit()
    if request.form.get('notify', '1') == '1':
        mail_handed(c)
    flash(f'سُلّمت العهدة {c.ref_code}', 'success')
    return redirect(url_for('custody.index'))


@custody_bp.route('/custody/<int:cid>', methods=['GET', 'POST'])
@permission_required(*ANY)
def edit(cid):
    user = current_user()
    c = _get(cid)
    if not (user.has_perm('custody.view_all') or user.has_perm('custody.manage_all')
            or c.department_id in set(user.department_ids)):
        abort(403)
    errors = []
    if request.method == 'POST':
        if not _can_manage(user, c):
            abort(403)
        errors = _apply(c, request.form, user)
        if not errors:
            audit('custody.updated', 'custody', c.id)
            db.session.commit()
            flash('تم الحفظ', 'success')
            return redirect(url_for('custody.index'))
        db.session.rollback()
    return render_template('custody/edit.html', c=c, errors=errors, departments=_depts_for(user),
                           employees=Employee.query.filter_by(is_active=True).order_by(Employee.full_name).all(),
                           can_manage=_can_manage(user, c))


def _return(c, by_name, by_email):
    c.status, c.returned_at = 'returned', date.today()
    c.returned_to = (by_name or '').strip()[:200] or current_user().full_name
    audit('custody.returned', 'custody', c.id, item=c.item)


@custody_bp.route('/custody/bulk', methods=['POST'])
@permission_required('custody.manage', 'custody.manage_all')
def bulk():
    user = current_user()
    act = request.form.get('action')
    if act not in ('return', 'handed', 'delete'):
        abort(400)
    done, skipped, returned = 0, [], []
    for c in Custody.query.filter(Custody.id.in_(_ids())).all():
        if not _can_manage(user, c):
            skipped.append(f'{c.item} (ليست من أقسامك)')
            continue
        if act == 'return':
            if c.status == 'returned':
                continue
            _return(c, request.form.get('returned_to'), user.email)
            returned.append(c)
        elif act == 'handed':                        # undo a return entered by mistake
            c.status, c.returned_at, c.returned_to = 'handed', None, None
            audit('custody.return_undone', 'custody', c.id)
        else:
            audit('custody.deleted', 'custody', c.id, item=c.item, recipient=c.recipient_name)
            db.session.delete(c)
        done += 1
    db.session.commit()
    for c in returned:
        mail_returned(c, user.email)
    verb = {'return': 'استُرجع', 'handed': 'أُعيد إلى «مسلمة»', 'delete': 'حُذف'}[act]
    flash(f'{verb}: {done}' + ((' — تُرك: ' + '، '.join(skipped[:10])) if skipped else ''),
          'success' if done else 'warning')
    return redirect(request.referrer or url_for('custody.index'))


@custody_bp.route('/custody/<int:cid>/return', methods=['POST'])
@permission_required('custody.manage', 'custody.manage_all')
def return_one(cid):
    c = _get(cid)
    if not _can_manage(current_user(), c):
        abort(403)
    if c.status != 'returned':
        _return(c, request.form.get('returned_to'), current_user().email)
        db.session.commit()
        mail_returned(c, current_user().email)
        flash('تم استرجاع العهدة وإرسال الإشعارات', 'success')
    return redirect(request.referrer or url_for('custody.index'))


@custody_bp.route('/custody/receipt')
@login_required
def receipt():
    """Printable receipt(s): ?ids=1&ids=2 …"""
    user = current_user()
    ids = [int(i) for i in request.args.getlist('ids') if i.isdigit()]
    rows = Custody.query.filter(Custody.id.in_(ids or [0])).all()
    mine = {e.id for e in Employee.query.filter_by(user_id=user.id).all()}
    allowed = [c for c in rows if user.has_perm('custody.view_all') or user.has_perm('custody.manage_all')
               or c.department_id in set(user.department_ids) or c.employee_id in mine]
    if not allowed:
        abort(404)
    return render_template('custody/receipt.html', rows=allowed)


@custody_bp.route('/custody/import', methods=['POST'])
@permission_required('custody.manage', 'custody.manage_all', 'custody.import')
def import_list():
    user = current_user()
    try:
        rows = read_table(request.form.get('text'), request.files.get('file'))
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('custody.index'))
    except Exception:  # noqa: BLE001
        flash('تعذّرت قراءة الملف — تأكد أنه ملف Excel أو CSV سليم', 'danger')
        return redirect(url_for('custody.index'))
    records, _ = to_records(rows, FIELDS)
    result = import_records(records, user, default_dept_id=request.form.get('department_id'))
    flash(f'أُضيف {result["added"]}، تُرك {result["skipped"]} (موجود مسبقًا)', 'success' if result['added'] else 'warning')
    for p in result['problems'][:15]:
        flash(p, 'danger')
    return redirect(url_for('custody.index'))


def import_records(records, user=None, default_dept_id=None):
    """Shared by the web import and ops/import_sheets.py. Recipients are matched to the roster by e-mail,
    then by name; departments by name or code."""
    from blueprints.employees import dept_resolver
    find_dept = dept_resolver()
    ids = manageable_dept_ids(user) if user else None
    emps_by_email = {e.email.lower(): e for e in Employee.query.filter(Employee.email.isnot(None)).all()}
    emps_by_name = {norm(e.full_name): e for e in Employee.query.all()}
    default = get_scoped(Department, int(default_dept_id)) if str(default_dept_id or '').isdigit() else None
    added = skipped = 0
    problems = []
    for i, r in enumerate(records, 1):
        if not r.get('item') or not r.get('recipient'):
            problems.append(f'سطر {i}: العهدة والمستلم مطلوبان')
            continue
        if r.get('ref_code') and Custody.query.filter_by(ref_code=r['ref_code']).first():
            skipped += 1
            continue
        dept = find_dept(r.get('department')) or default
        if dept is None or (ids is not None and dept.id not in ids):
            problems.append(f'سطر {i} ({r["item"]}): القسم «{r.get("department") or "—"}» غير معروف أو ليس من أقسامك')
            continue
        email = (r.get('recipient_email') or '').strip() or None
        emp = (emps_by_email.get(email.lower()) if email else None) or emps_by_name.get(norm(r['recipient']))
        status = 'returned' if norm(r.get('status')) in (norm('مسترجعة'), 'returned') else 'handed'
        try:
            qty = max(int(float(r.get('quantity') or 1)), 1)
        except ValueError:
            qty = 1
        c = Custody(ref_code=r.get('ref_code') or new_ref(), item=r['item'][:300], quantity=qty,
                    serial_no=(r.get('serial_no') or '')[:100] or None, employee_id=emp.id if emp else None,
                    recipient_name=r['recipient'][:200], recipient_email=email or (emp.email if emp else None),
                    department_id=dept.id, handed_by=(r.get('handed_by') or '')[:200] or None,
                    handed_by_email=r.get('handed_by_email') or None,
                    handed_at=parse_date(r.get('handed_at')), status=status,
                    returned_at=parse_date(r.get('returned_at')) if status == 'returned' else None,
                    returned_to=(r.get('returned_to') or '')[:200] or None if status == 'returned' else None)
        db.session.add(c)
        db.session.flush()
        added += 1
    audit('custody.imported', added=added, skipped=skipped, problems=len(problems))
    db.session.commit()
    return {'added': added, 'skipped': skipped, 'problems': problems}


# ───────────────────────────── my custody ─────────────────────────────
@custody_bp.route('/my')
@login_required
def mine():
    user = current_user()
    emp_ids = [e.id for e in Employee.query.filter_by(user_id=user.id).all()]
    q = Custody.query.filter(Custody.employee_id.in_(emp_ids or [0]))
    if user.email:
        q = Custody.query.filter(Custody.employee_id.in_(emp_ids or [0]) |
                                 (db.func.lower(Custody.recipient_email) == user.email.lower()))
    from models import ClearanceForm
    forms = ClearanceForm.query.filter(ClearanceForm.employee_id.in_(emp_ids or [0])).all()
    return render_template('custody/mine.html', rows=q.order_by(Custody.status, Custody.id.desc()).all(), forms=forms)
