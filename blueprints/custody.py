from flask import (Blueprint, render_template, request, redirect, url_for, flash, g, abort)
from sqlalchemy import func, or_

from authz import login_required, can_access_department
from extensions import db
from models import Custody, Department, User
from services import custody as svc

custody_bp = Blueprint('custody', __name__)

OWN, ALL_MANAGE, ALL_VIEW = 'custody.manage', 'custody.manage_all', 'custody.view_all'


def _flash_report(report, ok):
    if report.failures:
        flash(f'{ok}، لكن تعذّر إرسال: {report.failure_text()}', 'warning')
    else:
        flash(ok, 'success')


def _visible_departments(user):
    """Departments whose custody list this user may see."""
    q = Department.query.order_by(Department.sort_order, Department.name)
    if user.has_perm(ALL_VIEW) or user.has_perm(ALL_MANAGE):
        return q.all()
    if user.has_perm(OWN):
        return [d for d in q.all() if d.id in user.department_ids]
    return []


def _manageable_departments(user):
    q = Department.query.filter_by(is_active=True).order_by(Department.sort_order, Department.name)
    if user.has_perm(ALL_MANAGE):
        return q.all()
    if user.has_perm(OWN):
        return [d for d in q.all() if d.id in user.department_ids]
    return []


def _can_manage(user, c):
    return c.department_id is not None and can_access_department(user, c.department_id, OWN,
                                                                 ALL_MANAGE)


def _is_own(user, c):
    if c.recipient_user_id == user.id:
        return True
    return bool(user.email and c.recipient_email and user.email.lower() == c.recipient_email.lower())


def can_view(user, c):
    if user.has_perm(ALL_VIEW) or _can_manage(user, c):
        return True
    return _is_own(user, c)


def _get(cid):
    c = db.session.get(Custody, cid)
    if c is None:
        abort(404)
    return c


@custody_bp.route('/')
@login_required
def index():
    depts = _visible_departments(g.user)
    if not depts:
        return redirect(url_for('custody.mine'))
    ids = [d.id for d in depts]
    dept_id = request.args.get('dept', type=int)
    status = request.args.get('status') or ''
    q = (request.args.get('q') or '').strip()
    query = Custody.query.filter(Custody.department_id.in_(ids))
    if dept_id in ids:
        query = query.filter(Custody.department_id == dept_id)
    if status in ('handed', 'returned'):
        query = query.filter(Custody.status == status)
    if q:
        like = f'%{q.lower()}%'
        query = query.filter(or_(func.lower(Custody.item_name).like(like),
                                 func.lower(Custody.recipient_name).like(like),
                                 func.lower(Custody.code).like(like)))
    rows = svc.ordered(query).limit(500).all()
    return render_template('custody/list.html', rows=rows, depts=depts, dept_id=dept_id,
                           status=status, q=q, can_add=bool(_manageable_departments(g.user)))


@custody_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    depts = _manageable_departments(g.user)
    if not depts:
        abort(403)
    error = None
    data = request.form if request.method == 'POST' else {}
    if request.method == 'POST':
        dept_id = request.form.get('department_id', type=int)
        dept = next((d for d in depts if d.id == dept_id), None)
        if dept is None:
            abort(403)                    # not one of MY departments
        try:
            c, report = svc.add_custody(request.form, g.user, dept)
            _flash_report(report, f'تم تسليم العهدة ({c.code}) وحفظها')
            return redirect(url_for('custody.detail', cid=c.id))
        except svc.CustodyError as e:
            db.session.rollback()
            error = str(e)
    users = User.query.filter_by(is_active=True).order_by(User.full_name).all()
    return render_template('custody/new.html', depts=depts, data=data, error=error, users=users), \
        (400 if error else 200)


@custody_bp.route('/<int:cid>')
@login_required
def detail(cid):
    c = _get(cid)
    if not can_view(g.user, c):
        abort(403)
    return render_template('custody/detail.html', c=c, can_manage=_can_manage(g.user, c))


@custody_bp.route('/<int:cid>/return', methods=['POST'])
@login_required
def do_return(cid):
    c = _get(cid)
    if not _can_manage(g.user, c):
        abort(403)
    try:
        report = svc.return_custody(c, request.form.get('returned_to_name'),
                                    request.form.get('returned_to_email'), g.user)
        _flash_report(report, 'تم الاسترجاع')
    except svc.CustodyError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    return redirect(url_for('custody.detail', cid=cid))


@custody_bp.route('/<int:cid>/print')
@login_required
def print_receipt(cid):
    c = _get(cid)
    if not can_view(g.user, c):
        abort(403)
    return render_template('custody/print.html', c=c)


@custody_bp.route('/mine')
@login_required
def mine():
    u = g.user
    cond = [Custody.recipient_user_id == u.id]
    if u.email:
        cond.append(func.lower(Custody.recipient_email) == u.email.lower())
    rows = svc.ordered(Custody.query.filter(or_(*cond))).all()
    return render_template('custody/mine.html', rows=rows)
