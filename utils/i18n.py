"""اللغتان الرسميتان: العربية (افتراضياً) والإنجليزية، مع مبدّل في كل الصفحات.

    في القوالب:   {{ L('حفظ', 'Save') }}      ، و{{ lang }} و{{ dir }} لوسم الصفحة
    في الكود:     flash(pick('حُفظ.', 'Saved.'))
اللغة في الجلسة (session['lang']) وفي ملف تعريف ارتباط يبقى سنة، فتبقى بعد تسجيل الخروج.
البريد يبقى بالعربية الفصحى لأنه يصل إلى أقسام وموظفين لا نعرف لغة كل منهم.
"""
from flask import has_request_context, request, session


def lang():
    if not has_request_context():
        return 'ar'
    v = session.get('lang') or request.cookies.get('lang')
    return 'en' if v == 'en' else 'ar'


def pick(ar, en):
    return en if lang() == 'en' else ar


def L(ar, en):
    return pick(ar, en)


def tenant_name(tenant):
    if not tenant:
        return pick('براءة الذمة والعهد', 'Clearance and custody')
    return (tenant.name_en or tenant.name_ar) if lang() == 'en' else tenant.name_ar
