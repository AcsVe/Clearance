"""العرض للمستخدم: الأوقات بتوقيت عمّان، وسجل التدقيق بعبارات عربية لا برموز."""
from datetime import datetime

from extensions import db
from models import AuditLog
from tenancy import tenant_context
from tests.conftest import login


def test_local_time_filter(app):
    f = app.jinja_env.filters['local']
    assert f(datetime(2026, 12, 31, 22, 30)) == '2027-01-01 01:30'          # الأردن UTC+3 طوال السنة
    assert f(datetime(2026, 7, 1, 9, 0), '%H:%M') == '12:00'
    assert f(None) == ''


def test_audit_log_is_readable(app, two_tenants):
    with tenant_context(two_tenants['a']):
        db.session.add(AuditLog(action='clearance.responded', actor='النظام', entity_type='clearance', entity_id=7,
                                details={'department': 'المكتبة', 'status': 'held', 'count': 1}))
        db.session.commit()
    c = app.test_client()
    login(c, 'admin', org='a')
    page = c.get('/admin/audit').get_data(as_text=True)
    assert 'ردّ قسم على براءة الذمة' in page and 'نموذج رقم 7' in page and 'معلّق' in page and 'المكتبة' in page
    assert 'clearance.responded' not in page and '{"' not in page and 'UTC' not in page
    page = c.get('/admin/audit?action=auth.').get_data(as_text=True)
    assert 'ردّ قسم على براءة الذمة' not in page
