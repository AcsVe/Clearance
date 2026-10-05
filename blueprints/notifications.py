from flask import (Blueprint, render_template, request, redirect, url_for, g, abort, jsonify,
                   current_app)

from authz import login_required
from extensions import db
from models import Notification, PushSubscription
from models.core import utcnow

notifications_bp = Blueprint('notifications', __name__)


def _safe_link(link):
    return link if link and link.startswith('/') and not link.startswith('//') else url_for('main.home')


@notifications_bp.route('/')
@login_required
def index():
    rows = (Notification.query.filter_by(user_id=g.user.id)
            .order_by(Notification.id.desc()).limit(100).all())
    from services.notify import push_configured
    return render_template('notifications.html', rows=rows, push_enabled=push_configured())


@notifications_bp.route('/<int:nid>/open')
@login_required
def open_one(nid):
    n = Notification.query.filter_by(id=nid, user_id=g.user.id).first()
    if n is None:
        abort(404)
    if n.read_at is None:
        n.read_at = utcnow()
        db.session.commit()
    return redirect(_safe_link(n.link))


@notifications_bp.route('/read-all', methods=['POST'])
@login_required
def read_all():
    Notification.query.filter_by(user_id=g.user.id, read_at=None) \
        .update({'read_at': utcnow()}, synchronize_session=False)
    db.session.commit()
    return redirect(url_for('notifications.index'))


@notifications_bp.route('/vapid-key')
@login_required
def vapid_key():
    key = current_app.config.get('VAPID_PUBLIC_KEY')
    if not key:
        abort(404)
    return jsonify({'key': key})


@notifications_bp.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint') or ''
    keys = data.get('keys') or {}
    if not endpoint.startswith('https://') or not keys.get('p256dh') or not keys.get('auth'):
        abort(400)
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub is None:
        sub = PushSubscription(endpoint=endpoint)
        db.session.add(sub)
    sub.user_id, sub.p256dh, sub.auth = g.user.id, keys['p256dh'][:255], keys['auth'][:255]
    db.session.commit()
    return jsonify({'ok': True})


@notifications_bp.route('/unsubscribe', methods=['POST'])
@login_required
def unsubscribe():
    endpoint = (request.get_json(silent=True) or {}).get('endpoint') or ''
    PushSubscription.query.filter_by(endpoint=endpoint, user_id=g.user.id).delete()
    db.session.commit()
    return jsonify({'ok': True})
