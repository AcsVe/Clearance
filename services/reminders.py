"""Scheduled jobs. Each runs per organization inside tenant_context (see
scheduler.run_for_all_tenants), so tenant isolation applies here too."""
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo

from flask import current_app

from extensions import db
from models import Custody
from models.core import utcnow
from scheduler import register_job
from services.notify import notify, users_with_perm
from utils.audit import audit
from utils.helpers import split_emails
from utils.mail import send_email


@register_job(minutes=60)
def remind_overdue_custody(app, tenant):
    """Custody past its expected return date: email the recipient and the
    department, notify the department's custody managers. Repeats every
    OVERDUE_REMINDER_DAYS days until the item is returned."""
    cfg = current_app.config
    today = datetime.now(ZoneInfo(cfg.get('TIMEZONE', 'Asia/Amman'))).date()
    every = timedelta(days=int(cfg.get('OVERDUE_REMINDER_DAYS', 7)))
    now = utcnow()
    due = (Custody.query.filter(Custody.status == 'handed',
                                Custody.expected_return_date.isnot(None),
                                Custody.expected_return_date < today)
           .filter((Custody.last_reminder_at.is_(None)) | (Custody.last_reminder_at <= now - every))
           .order_by(Custody.id).all())
    base = cfg.get('BASE_URL', '')
    sent = 0
    for c in due:
        c.last_reminder_at = now
        db.session.commit()
        days = (today - c.expected_return_date).days
        to = split_emails(','.join(filter(None, [c.recipient_email or '',
                                                 c.department.emails if c.department else ''])))
        send_email(to, f'تذكير: عهدة متأخرة عن موعد الإرجاع — {c.item_name}',
                   'emails/custody_overdue.html', tenant, kind='custody_overdue',
                   entity=('custody', c.id), c=c, days=days, link=f'{base}/custody/{c.id}/print')
        if c.department_id:
            notify(users_with_perm('custody.manage', c.department_id),
                   f'عهدة متأخرة: {c.item_name}',
                   f'لدى {c.recipient_name} — متأخرة {days} يومًا', f'/custody/{c.id}',
                   'custody_overdue')
        sent += 1
    if sent:
        audit('custody.overdue_reminders', count=sent, actor='المجدول')
        db.session.commit()
    return sent
