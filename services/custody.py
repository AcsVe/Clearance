"""Business rules of العهد العينية, ported from addCustody, returnCustody,
getDepartmentCustodies and printCustodyReceipt."""
from datetime import date

from flask import current_app, g
from sqlalchemy import case, func

from extensions import db
from models import Custody, Department, User
from models.core import utcnow
from services.clearance import MailReport
from utils.audit import audit
from utils.helpers import short_code, is_valid_email, split_emails
from utils.mail import send_email


class CustodyError(Exception):
    pass


def _unique_code():
    for _ in range(20):
        code = 'CUST-' + short_code()
        if not Custody.query.filter_by(code=code).first():
            return code
    raise CustodyError('تعذر توليد رقم عهدة فريد')


def receipt_link(c):
    base = current_app.config.get('BASE_URL')
    if not base:
        raise CustodyError('BASE_URL غير مضبوط؛ لا يمكن بناء رابط الإشعار')
    return f'{base}/custody/{c.id}/print'


def ordered(query):
    """getDepartmentCustodies() order: handed-out first, then newest."""
    return query.order_by(case((Custody.status == 'handed', 0), else_=1), Custody.id.desc())


def _match_user(email):
    if not email:
        return None
    return User.query.filter(func.lower(User.email) == email.lower()).first()


def add_custody(data, actor, department):
    item = (data.get('item_name') or '').strip()
    recipient = (data.get('recipient_name') or '').strip()
    r_email = (data.get('recipient_email') or '').strip()
    h_name = (data.get('handed_by_name') or '').strip() or actor.full_name
    h_email = (data.get('handed_by_email') or '').strip() or (actor.email or '')
    expected = (data.get('expected_return_date') or '').strip()
    errors = []
    if not item:
        errors.append('وصف العهدة مطلوب')
    if not recipient:
        errors.append('اسم المستلم مطلوب')
    for label, e in (('بريد المستلم', r_email), ('بريد مسلّم العهدة', h_email)):
        if e and not is_valid_email(e):
            errors.append(f'{label} غير صالح')
    exp_date = None
    if expected:
        try:
            exp_date = date.fromisoformat(expected)
        except ValueError:
            errors.append('تاريخ الإرجاع المتوقع غير صالح')
    if errors:
        raise CustodyError('، '.join(errors))

    match = _match_user(r_email)
    c = Custody(code=_unique_code(), item_name=item, recipient_name=recipient,
                recipient_email=r_email or None, recipient_user_id=match.id if match else None,
                department_id=department.id, department_name=department.name,
                department_code=department.code, handed_by_name=h_name,
                handed_by_email=h_email or None, handed_by_user_id=actor.id,
                handed_at=utcnow(), expected_return_date=exp_date, status='handed',
                notes=(data.get('notes') or '').strip() or None, source='manual')
    db.session.add(c)
    db.session.flush()
    audit('custody.handed', 'custody', c.id, code=c.code, item=item, recipient=recipient,
          department=department.name)
    db.session.commit()

    link = receipt_link(c)
    report = MailReport()
    if c.recipient_email:
        report.add('بريد المستلم', send_email(
            [c.recipient_email], f'إشعار استلام عهدة: {c.item_name}',
            'emails/custody_handed_recipient.html', g.tenant, kind='custody_handed',
            entity=('custody', c.id), c=c, link=link))
    mgmt = split_emails(','.join(filter(None, [department.emails or '', c.handed_by_email or ''])))
    if mgmt:
        report.add('القسم ومسلّم العهدة', send_email(
            mgmt, f'إشعار تسليم عهدة: {c.recipient_name}',
            'emails/custody_handed_manager.html', g.tenant, kind='custody_handed',
            entity=('custody', c.id), c=c, link=link))
    return c, report


def return_custody(c, returned_to_name, returned_to_email, actor):
    if c.status == 'returned':
        raise CustodyError('العهدة مسترجعة مسبقًا')
    name = (returned_to_name or '').strip() or actor.full_name
    email = (returned_to_email or '').strip() or (actor.email or '')
    if email and not is_valid_email(email):
        raise CustodyError('بريد مستلم الإرجاع غير صالح')
    c.status, c.returned_at = 'returned', utcnow()
    c.returned_to_name, c.returned_to_email = name, email or None
    c.returned_by_user_id = actor.id
    audit('custody.returned', 'custody', c.id, code=c.code, returned_to=name)
    db.session.commit()

    dept = c.department
    recipients = split_emails(','.join(filter(None, [
        c.recipient_email or '', dept.emails if dept else '', c.handed_by_email or '', email])))
    report = MailReport()
    if recipients:
        report.add('إشعار الاسترجاع', send_email(
            recipients, f'إشعار استرجاع عهدة: {c.item_name}', 'emails/custody_returned.html',
            g.tenant, kind='custody_returned', entity=('custody', c.id), c=c,
            link=receipt_link(c)))
    return report
