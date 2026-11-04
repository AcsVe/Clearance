"""مراجعة المظهر في متصفح حقيقي (Chrome المثبّت عبر Playwright): كل صفحة على الحاسوب والهاتف.

يقيس: تباين النص مع خلفيته (4.5 على الأقل، و3 للنص الكبير)، والخط الأصغر من 13 بكسل، والتمرير الأفقي، والعناصر
الخارجة عن بطاقتها، وأخطاء JavaScript وسياسة أمان المحتوى. ويحفظ صوراً مصغّرة إن ضُبط SHOTS=1.

    python tests/ui_audit.py            (بيئة فيها playwright؛ CLR_PY = بايثون النظام لتشغيل الخادم والبيانات)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get('E2E_OUT') or tempfile.mkdtemp(prefix='clr-ui-')
os.makedirs(OUT, exist_ok=True)
DB = os.path.join(OUT, 'ui.db')
for ext in ('', '-wal', '-shm'):
    if os.path.exists(DB + ext):
        os.remove(DB + ext)
PY = os.environ.get('CLR_PY') or sys.executable
PORT = 5096
BASE = f'http://127.0.0.1:{PORT}'
ENV = dict(os.environ, PYTHONUTF8='1', SECRET_KEY='ui-audit-secret', BASE_URL=BASE, PORT=str(PORT), CLR_DB_PATH=DB,
           DATABASE_URL='sqlite:///' + DB.replace('\\', '/'), DATA_DIR=OUT, DISABLE_SCHEDULER='1', DISABLE_REMINDERS='1',
           TRUST_PROXY='0', CONFIG_ENV=os.path.join(OUT, 'none.env'))
for k in ('MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL'):
    ENV.pop(k, None)
ids = json.loads(subprocess.run([PY, os.path.join(ROOT, 'tests', 'ui_seed.py'), DB], env=ENV, cwd=ROOT, capture_output=True,
                                text=True, encoding='utf-8', check=True).stdout.strip().splitlines()[-1])
srv = subprocess.Popen([PY, os.path.join(ROOT, 'serve.py')], env=ENV, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(80):
    try:
        urllib.request.urlopen(BASE + '/login', timeout=1)
        break
    except Exception:
        time.sleep(0.5)

PAGES = ['/home', '/clearance/', '/clearance/new', f'/clearance/{ids["form"]}', f'/clearance/{ids["form"]}/print',
         '/departments/portal', '/finance', '/custody/', '/my', f'/custody/receipt?ids={ids["custody"]}',
         '/employees/', f'/employees/{ids["emp"]}', '/admin/departments', '/admin/departments/new',
         f'/admin/departments/{ids["dept"]}', '/admin/users', '/admin/users/new', '/admin/roles', '/admin/roles/new',
         '/admin/settings', '/admin/email-log', '/admin/audit', '/change-password']
PUBLIC = ['/login', '/forgot', f'/sign/{ids["sign"]}', '/super/login', '/no-such-page']

AUDIT_JS = r"""() => {
  const lum = (c) => { const m = c.match(/[\d.]+/g); if (!m) return null; const [r, g, b] = m.slice(0, 3).map((v) => { v = v / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return { l: 0.2126 * r + 0.7152 * g + 0.0722 * b, a: m.length > 3 ? Number(m[3]) : 1 }; };
  const bgOf = (el) => { while (el) { const s = getComputedStyle(el); const L = lum(s.backgroundColor);
      if (s.backgroundImage !== 'none') return null; if (L && L.a > 0.9) return L.l; el = el.parentElement; } return 1; };
  const out = { contrast: [], small: [], spill: [] }; const seen = new Set();
  document.querySelectorAll('body *').forEach((el) => {
    if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') return;
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1)) return;
    const s = getComputedStyle(el); if (s.visibility === 'hidden' || Number(s.opacity) < 0.3) return;
    if (el.closest('canvas,svg,option,select')) return;
    const fs = parseFloat(s.fontSize), fw = Number(s.fontWeight) || 400, txt = el.textContent.trim().slice(0, 40);
    const fg = lum(s.color), bg = bgOf(el);
    if (fg && bg !== null && !el.closest(':disabled,[disabled]')) {
      const ratio = (Math.max(fg.l, bg) + 0.05) / (Math.min(fg.l, bg) + 0.05), large = fs >= 24 || (fs >= 18.6 && fw >= 700);
      if (ratio < (large ? 3 : 4.5) && !seen.has('c' + txt)) { seen.add('c' + txt); out.contrast.push(`${ratio.toFixed(2)} «${txt}» ${el.tagName}.${el.className}`.slice(0, 140)); }
    }
    if (fs < 13 && !seen.has('s' + txt)) { seen.add('s' + txt); out.small.push(`${fs}px «${txt}» ${el.tagName}.${el.className}`.slice(0, 120)); }
  });
  document.querySelectorAll('.card, .kpi, .stat, .auth-card').forEach((card) => {
    const cr = card.getBoundingClientRect(); if (!cr.width) return;
    card.querySelectorAll('*').forEach((el) => {
      if (el.closest('.table-wrap,.dt-wrap,.tbl-wrap,[style*="overflow"],details:not([open]),[hidden],.menu,dialog')) return;
      const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
      if ((r.left < cr.left - 2 || r.right > cr.right + 2) && !seen.has('p' + el.tagName + el.className)) { seen.add('p' + el.tagName + el.className);
        out.spill.push(`${el.tagName}.${el.className} «${(el.textContent || '').trim().slice(0, 30)}» in ${card.className}`.slice(0, 140)); }
    });
  });
  out.overflow = document.documentElement.scrollWidth - innerWidth;
  return out;
}"""

report = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get('E2E_CHANNEL', 'chrome'))
        for vp_name, vp in (('desktop', {'viewport': {'width': 1366, 'height': 900}}), ('phone', dict(p.devices['iPhone 13']))):
            ctx = browser.new_context(**vp)
            page = ctx.new_page()
            errs = []
            page.on('pageerror', lambda e: errs.append(str(e)))
            page.on('response', lambda r: errs.append(f'HTTP {r.status} {r.request.method} {r.url.replace(BASE, "")}') if r.status >= 400 and '/no-such-page' not in r.url else None)

            def visit(u):
                errs.clear()
                page.goto(BASE + u, wait_until='networkidle')
                page.wait_for_timeout(250)
                report.append((vp_name, u, page.evaluate(AUDIT_JS), list(errs)))
                if os.environ.get('SHOTS'):
                    name = f"{vp_name}-{u.strip('/').replace('/', '_').replace('?', '_')[:40] or 'root'}.jpg"
                    try:
                        page.screenshot(path=os.path.join(OUT, name), type='jpeg', quality=40, full_page=True)
                    except Exception:
                        page.screenshot(path=os.path.join(OUT, name), type='jpeg', quality=40)
            for u in PUBLIC:
                visit(u)
            page.goto(BASE + '/login')
            if page.locator('[name=org]').count():
                page.fill('[name=org]', 'raed')
            page.fill('[name=username]', 'admin')
            page.fill('[name=password]', 'Strong-Pass-2026')
            page.click('button[type=submit]')
            page.wait_for_load_state('networkidle')
            for u in PAGES:
                visit(u)
            ctx.close()
        browser.close()
finally:
    srv.terminate()

bad = 0
for vp_name, u, res, errs in report:
    issues = ([f"horizontal overflow {res['overflow']}px"] if res['overflow'] > 2 else []) + ['contrast ' + x for x in res['contrast']] \
        + ['small ' + x for x in res['small']] + ['spill ' + x for x in res['spill']] + ['JS ' + e for e in errs]
    if issues:
        bad += len(issues)
        print(f'[{vp_name}] {u}')
        for i in issues[:15]:
            print('   ', i)
print(f'\n{len(report)} page views, {bad} issue(s). Output: {OUT}')
sys.exit(1 if bad else 0)
