from datetime import date, timedelta

import pytest
from flask import g

from extensions import db
from models import Notification, PushSubscription, Custody, EmailLog, Tenant, User
from models.core import utcnow
from tenancy import tenant_context
from tests.conftest import login, make_user
from tests.test_clearance import org, client_for, outbox, create, sign  # noqa: F401
from tests.test_custody import hand, cid_from


def notes_for(org, user_key):
    with tenant_context(org['t']):
        return [n.title for n in Notification.query.filter_by(user_id=org[user_key])
                .order_by(Notification.id).all()]


def test_clearance_notifications_go_to_the_right_people(app, org):
    _, fid = create(app)
    assert any('طلب توقيع' in t for t in notes_for(org, 'it_mgr'))
    assert any('طلب توقيع' in t for t in notes_for(org, 'lib_mgr'))
    assert notes_for(org, 'emp') == []
    hr_before = len(notes_for(org, 'hr'))
    sign(app, 'itmgr', fid, org['it'])
    sign(app, 'libmgr', fid, org['lib'], 'held', 'كتاب')
    sign(app, 'fin', fid, org['fin_d'])
    sign(app, 'hr', fid, org['hr_d'])            # HR completes it: not notified of own action
    assert not any('اكتملت' in t for t in notes_for(org, 'hr')[hr_before:])
    assert any('بانتظار التسوية' in t for t in notes_for(org, 'fin'))
    client_for(app, 'fin').post(f'/clearance/{fid}/settle', data={'note': 'تم'})
    assert any('تمت التسوية' in t for t in notes_for(org, 'hr'))


def test_custody_notifications_scoped_to_department(app, org):
    with tenant_context(org['t']):
        tenant = db.session.get(Tenant, org['t'])
    make_user(tenant, 'itmgr2', ('dept_manager',), (org['it'],))
    c_id = cid_from(hand(app, 'itmgr', org['it']))
    with tenant_context(org['t']):
        other = User.query.filter_by(username='itmgr2').one().id
        assert Notification.query.filter_by(user_id=other, kind='custody_handed').count() == 1
        assert Notification.query.filter_by(user_id=org['it_mgr'], kind='custody_handed').count() == 0
        assert Notification.query.filter_by(user_id=org['lib_mgr'], kind='custody_handed').count() == 0
    client_for(app, 'itmgr').post(f'/custody/{c_id}/return', data={})
    with tenant_context(org['t']):
        assert Notification.query.filter_by(user_id=other, kind='custody_returned').count() == 1


def test_bell_page_and_read(app, org):
    create(app)
    c = client_for(app, 'itmgr')
    html = c.get('/home').get_data(as_text=True)
    assert 'class="count">1<' in html
    with tenant_context(org['t']):
        n = Notification.query.filter_by(user_id=org['it_mgr']).first()
    r = c.get(f'/notifications/{n.id}/open')
    assert r.status_code == 302 and '/sign/' in r.headers['Location']
    assert 'class="count"' not in c.get('/home').get_data(as_text=True)
    # someone else's notification
    assert client_for(app, 'libmgr').get(f'/notifications/{n.id}/open').status_code == 404


def test_notification_link_cannot_redirect_offsite(app, org):
    with tenant_context(org['t']):
        db.session.add(Notification(user_id=org['emp'], title='x', link='//evil.example/'))
        db.session.commit()
        nid = Notification.query.filter_by(user_id=org['emp']).one().id
    r = client_for(app, 'emp').get(f'/notifications/{nid}/open')
    assert r.headers['Location'].endswith('/home')


def test_overdue_custody_reminder(app, org):
    from services.reminders import remind_overdue_custody
    c_id = cid_from(hand(app, 'itmgr', org['it']))
    late = cid_from(hand(app, 'itmgr', org['it'], item='كاميرا'))
    returned = cid_from(hand(app, 'itmgr', org['it'], item='ميكروفون'))
    with tenant_context(org['t']):
        for cid, days in ((c_id, -3), (late, 5), (returned, 9)):
            db.session.get(Custody, cid).expected_return_date = date.today() - timedelta(days=days)
        db.session.commit()
    client_for(app, 'itmgr').post(f'/custody/{returned}/return', data={})
    app.config['MAIL_OUTBOX'] = []

    def run():
        with app.test_request_context('/'):
            with tenant_context(org['t']):
                return remind_overdue_custody(app, db.session.get(Tenant, org['t']))

    assert run() == 1                                 # only the late, not-returned one
    mails = outbox(app, 'custody_overdue')
    assert len(mails) == 1 and 'كاميرا' in mails[0]['html']
    assert sorted(mails[0]['to']) == ['it@raed.test', 'leaver@raed.test']
    assert run() == 0                                 # not again before OVERDUE_REMINDER_DAYS
    with tenant_context(org['t']):
        db.session.get(Custody, late).last_reminder_at = utcnow() - timedelta(days=8)
        db.session.commit()
    assert run() == 1


def test_scheduler_runs_each_tenant_isolated(app, org):
    from scheduler import run_for_all_tenants
    seen = []
    run_for_all_tenants(app, lambda a, t: seen.append((t.id, Custody.query.count())))
    assert seen == [(org['t'], 0)]


def test_email_failure_banner(app, org):
    app.config['MAIL_BACKEND'] = 'graph'            # not configured → fails and is logged
    create(app)
    html = client_for(app, 'hr').get('/home').get_data(as_text=True)
    assert 'رسالة بريد لم تُرسل' in html
    assert 'رسالة بريد لم تُرسل' not in client_for(app, 'itmgr').get('/home').get_data(as_text=True)
    client_for(app, 'hr').post('/admin/email-log/ack')
    assert 'رسالة بريد لم تُرسل' not in client_for(app, 'hr').get('/home').get_data(as_text=True)


def test_push_subscription_and_send(app, org, monkeypatch):
    import pywebpush
    from py_vapid import Vapid01
    from cryptography.hazmat.primitives import serialization
    import base64
    v = Vapid01()
    v.generate_keys()
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip('=')
    app.config['VAPID_PUBLIC_KEY'] = b64(v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    app.config['VAPID_PRIVATE_KEY'] = b64(v.private_key.private_numbers().private_value.to_bytes(32, 'big'))
    try:
        assert Vapid01.from_string(app.config['VAPID_PRIVATE_KEY'])   # format pywebpush accepts
        c = client_for(app, 'itmgr')
        assert c.get('/notifications/vapid-key').get_json()['key'] == app.config['VAPID_PUBLIC_KEY']
        assert c.post('/notifications/subscribe', json={'endpoint': 'http://x'}).status_code == 400
        sub = {'endpoint': 'https://push.example/abc', 'keys': {'p256dh': 'k', 'auth': 'a'}}
        assert c.post('/notifications/subscribe', json=sub).status_code == 200
        calls = []
        monkeypatch.setattr(pywebpush, 'webpush', lambda **kw: calls.append(kw))
        create(app)
        assert len(calls) == 1 and calls[0]['subscription_info']['endpoint'] == 'https://push.example/abc'
        assert 'طلب توقيع' in calls[0]['data']

        class Gone(pywebpush.WebPushException):
            pass

        def gone(**kw):
            raise Gone('gone', response=type('R', (), {'status_code': 410})())
        monkeypatch.setattr(pywebpush, 'webpush', gone)
        create(app, 'سارة')                                  # must not fail the action
        with tenant_context(org['t']):
            assert PushSubscription.query.count() == 0       # dead subscription removed
    finally:
        app.config['VAPID_PUBLIC_KEY'] = app.config['VAPID_PRIVATE_KEY'] = ''


def test_push_endpoints_need_login(app, org):
    assert app.test_client().post('/notifications/subscribe', json={}).status_code == 302
