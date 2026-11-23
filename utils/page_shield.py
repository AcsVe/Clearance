"""حماية مصدر الصفحات: كل صفحة HTML كاملة تُرسل مشفّرة، ويفكّها ملف `static/js/shield.js` في المتصفح.

• «عرض المصدر» يُظهر كتلة رموز فقط بدل الكود والنصوص.
• يمنع أيضاً الزر الأيمن واختصارات عرض المصدر والحفظ وأدوات المطوّر (إلا داخل خانات الكتابة).
• هذا النظام يطبّق سياسة أمان صارمة (`script-src 'self'`) فلا يُسمح بأي سكربت داخل الصفحة؛ لذلك الفك في ملف
  خارجي، والمحتوى المشفّر في خاصية (`data-p`) لا في سكربت.
• كشف أدوات المطوّر يعمل في لوحات الإدارة (`/admin` و`/super`) فقط.
• للتعطيل مؤقتاً: PAGE_SHIELD=0 في config.env — ولإيقاف كشف أدوات المطوّر فقط: DEVTOOLS_GUARD=0
• لا يلمس ردود JSON والملفات الثابتة (ومنها صفحة عدم الاتصال) وأجزاء HTML التي تُطلب بالجافاسكربت.

ملاحظة صريحة: لا شيء يخفي الكود نهائياً عن خبير — هذا يجعله غير مقروء لعامة الناس.
"""
import base64
import os
import random

_B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
_GUARD_PATHS = ('/admin', '/super')


def _encode(html_bytes):
    """base64 ← إزاحة عشوائية في الأبجدية ← عكس النص (يتغير شكل الكتلة مع كل طلب)"""
    s = base64.b64encode(html_bytes).decode('ascii')
    k = random.randint(1, 63)
    table = str.maketrans(_B64, _B64[k:] + _B64[:k])
    return s.translate(table)[::-1], k


def _shell(payload, k, guard_devtools):
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<script src="/static/js/shield.js" data-p="{payload}" data-k="{k}" data-g="{1 if guard_devtools else 0}">'
            '</script></head><body></body></html>').encode('utf-8')


def install(app):
    from flask import request

    @app.after_request
    def _page_shield(resp):
        if os.environ.get('PAGE_SHIELD', '1') == '0':
            return resp
        if app.testing and not app.config.get('PAGE_SHIELD'):
            return resp
        try:
            if request.endpoint == 'static':
                return resp
            if resp.mimetype != 'text/html' or resp.direct_passthrough or resp.is_streamed:
                return resp
            body = resp.get_data()
            head = body[:400].lstrip().lower()
            if not (head.startswith(b'<!doctype') or head.startswith(b'<html')):
                return resp          # جزء HTML يُطلب بالجافاسكربت — يبقى كما هو
            payload, k = _encode(body)
            guard = os.environ.get('DEVTOOLS_GUARD', '1') != '0' and request.path.startswith(_GUARD_PATHS)
            resp.set_data(_shell(payload, k, guard))
            resp.headers['Cache-Control'] = 'no-store'
        except Exception:
            pass
        return resp
