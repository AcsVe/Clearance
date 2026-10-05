"""In-app notifications (+ optional Web Push) for the people allowed to act.

Who gets what:
  custody handed / returned  → custody managers of that department
  custody overdue            → custody managers of that department
  clearance sign request     → signers linked to each department
  clearance completed        → users with clearance.manage (HR)
  clearance has held lines   → users with finance.settle
  settled                    → users with clearance.manage
Web Push is sent only when VAPID keys are configured; failures are logged
and dead subscriptions removed, never raised to the user's action.
"""
import json

from flask import current_app

from extensions import db
from models import Notification, PushSubscription, User


def users_with_perm(code, department_id=None, all_code=None):
    out = []
    for u in User.query.filter_by(is_active=True).all():
        if all_code and u.has_perm(all_code):
            out.append(u)
        elif u.has_perm(code) and (department_id is None or department_id in u.department_ids):
            out.append(u)
    return out


def push_configured():
    cfg = current_app.config
    return bool(cfg.get('VAPID_PRIVATE_KEY') and cfg.get('VAPID_PUBLIC_KEY'))


def _send_push(user_ids, title, body, link):
    if not push_configured() or not user_ids:
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        current_app.logger.warning('[push] pywebpush not installed')
        return 0
    cfg = current_app.config
    payload = json.dumps({'title': title, 'body': body or '', 'url': link or '/'}, ensure_ascii=False)
    sent = 0
    for sub in PushSubscription.query.filter(PushSubscription.user_id.in_(user_ids)).all():
        try:
            webpush(subscription_info=sub.to_push_dict(), data=payload,
                    vapid_private_key=cfg['VAPID_PRIVATE_KEY'],
                    vapid_claims={'sub': cfg.get('VAPID_CLAIMS_EMAIL') or 'mailto:admin@example.com'},
                    timeout=10)
            sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status in (404, 410):
                db.session.delete(sub)          # browser unsubscribed or expired
            current_app.logger.warning('[push] failed for user %s: %s', sub.user_id, e)
        except Exception as e:
            current_app.logger.warning('[push] failed for user %s: %s', sub.user_id, e)
    db.session.commit()
    return sent


def notify(users, title, body=None, link=None, kind=None, exclude_user_id=None):
    """Create one notification per user (deduplicated) and push it. The
    actor who caused the event is not notified about their own action."""
    ids = []
    for u in users:
        uid = u.id if hasattr(u, 'id') else int(u)
        if uid != exclude_user_id and uid not in ids:
            ids.append(uid)
    for uid in ids:
        db.session.add(Notification(user_id=uid, title=title[:200], body=(body or '')[:500] or None,
                                    link=link, kind=kind))
    db.session.commit()
    _send_push(ids, title, body, link)
    return ids


def unread_count(user):
    return Notification.query.filter_by(user_id=user.id, read_at=None).count()
