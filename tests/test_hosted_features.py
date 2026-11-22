"""ميزات النسخة المستضافة: اشتراك النسخة، وأيقونة العميل، ورقم النسخة، وحماية مصدر الصفحات."""
import os
import tempfile
from datetime import date, timedelta

import pytest

from tests.conftest import login, two_tenants  # noqa: F401


@pytest.fixture
def env(monkeypatch):
    for k in ('LICENSE_UNTIL', 'LICENSE_FILE', 'LICENSE_ID', 'LICENSE_WARN_DAYS', 'LICENSE_GRACE_DAYS',
              'LICENSE_CONTACT', 'FAVICON_FILE'):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _until(days):
    return (date.today() + timedelta(days=days)).isoformat()


def test_no_subscription_changes_nothing(app, env, two_tenants):
    c = app.test_client()
    login(c, 'admin', org='a')
    page = c.get('/home').get_data(as_text=True)
    assert 'license-alert' not in page and 'update-notice.js' in page


def test_banner_for_admins_only_when_near_the_end(app, env, two_tenants):
    env.setenv('LICENSE_UNTIL', _until(10))
    c = app.test_client()
    login(c, 'admin', org='a')
    assert 'license-alert' in c.get('/home').get_data(as_text=True)


def test_expired_subscription_locks_pages_but_keeps_login(app, env, two_tenants):
    env.setenv('LICENSE_UNTIL', _until(-30))
    env.setenv('LICENSE_GRACE_DAYS', '7')
    c = app.test_client()
    assert c.get('/login').status_code == 200            # الدخول يبقى مفتوحاً
    login(c, 'admin', org='a')
    r = c.get('/home')
    assert r.status_code == 403 and 'انتهى اشتراك هذا النظام' in r.get_data(as_text=True)
    assert c.get('/healthz').status_code == 200


def test_shared_file_wins_over_env(app, env, two_tenants):
    import json
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump({'rasc': {'until': _until(-30), 'grace_days': 0, 'warn_days': 30}}, f)
    try:
        env.setenv('LICENSE_UNTIL', _until(400))
        env.setenv('LICENSE_FILE', f.name)
        env.setenv('LICENSE_ID', 'rasc')
        c = app.test_client()
        login(c, 'admin', org='a')
        assert c.get('/home').status_code == 403
    finally:
        os.remove(f.name)


def test_client_icon_replaces_the_system_icon(app, env, two_tenants):
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(b'\x89PNG\r\n\x1a\n' + b'0' * 16)
    try:
        c = app.test_client()
        assert 'brand-favicon.png' not in c.get('/login').get_data(as_text=True)
        env.setenv('FAVICON_FILE', f.name)
        assert 'brand-favicon.png' in c.get('/login').get_data(as_text=True)
        r = c.get('/brand-favicon.png')
        assert r.status_code == 200 and r.mimetype == 'image/png'
    finally:
        os.remove(f.name)


def test_app_version_endpoint_matches_the_page(app, env, two_tenants):
    c = app.test_client()
    v = c.get('/api/app-version').get_json()['version']
    assert len(v) == 12
    login(c, 'admin', org='a')
    assert f'data-version="{v}"' in c.get('/home').get_data(as_text=True)


def test_page_shield_is_csp_safe(app, env, two_tenants):
    app.config['PAGE_SHIELD'] = True
    try:
        c = app.test_client()
        r = c.get('/login')
        body = r.get_data(as_text=True)
        assert '<title>' not in body and 'data-p="' in body and '/static/js/shield.js' in body
        # سياسة الأمان تمنع السكربت داخل الصفحة، فلا يجوز أن يحتوي الغلاف على أي سكربت مضمّن
        import re
        for m in re.finditer(r'<script([^>]*)>(.*?)</script>', body, re.S):
            assert 'src=' in m.group(1) and not m.group(2).strip()
        assert "script-src 'self'" in r.headers['Content-Security-Policy']
        assert c.get('/static/js/shield.js').status_code == 200          # الملفات الثابتة لا تُشفَّر
        assert c.get('/healthz').get_data(as_text=True).strip().startswith(('ok', '{'))
    finally:
        app.config['PAGE_SHIELD'] = False
