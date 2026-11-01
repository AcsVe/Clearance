"""استيراد بيانات Google Sheet القديمة (الأقسام، النماذج، الردود، العهد) إلى منظمة.

    python ops/import_sheets.py [ملف.xlsx] [الاسم المختصر للمنظمة]
حمِّل الملف من Google Sheets: ملف ← تنزيل ← Microsoft Excel (.xlsx). إعادة التشغيل لا تكرر ما استُورد.
يمكن الاستيراد أيضاً من داخل النظام: الإدارة ← الإعدادات ← «استيراد من Google Sheet».
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, load_env, t, die  # noqa: E402


def find_xlsx():
    home = os.path.expanduser('~')
    cands = []
    for folder in (BASE, os.path.join(home, 'Downloads'), os.path.join(home, 'Desktop')):
        cands += glob.glob(os.path.join(folder, '*.xlsx'))
    cands = [c for c in cands if not os.path.basename(c).startswith('~$')]
    return max(cands, key=os.path.getmtime) if cands else ''


def main():
    load_env()
    sys.path.insert(0, BASE)
    os.environ.setdefault('DISABLE_REMINDERS', '1')
    path = sys.argv[1] if len(sys.argv) > 1 else ''
    if not path:
        guess = find_xlsx()
        print(t('\n  ملف Excel المحمَّل من Google Sheet (ملف ← تنزيل ← Microsoft Excel)',
                '\n  The Excel file downloaded from the Google Sheet (File > Download > Microsoft Excel)'))
        path = input(f'  {t("المسار", "Path")}' + (f' [{guess}]' if guess else '') + ': ').strip().strip('"') or guess
    if not path or not os.path.isfile(path):
        die(t(f'الملف غير موجود: {path}', f'File not found: {path}'))
    from app import create_app
    from tenancy import tenant_bypass, tenant_context
    app = create_app({'DISABLE_REMINDERS': True})
    with app.app_context():
        from models import Tenant
        from services.sheets_import import import_workbook
        with tenant_bypass():
            tenants = Tenant.query.order_by(Tenant.id).all()
        if not tenants:
            die(t('لا توجد منظمة بعد — شغّل ops\\first_org.py أولاً', 'No organization yet - run ops\\first_org.py first'))
        slug = sys.argv[2] if len(sys.argv) > 2 else (tenants[0].slug if len(tenants) == 1 else
                                                      input(t('  الاسم المختصر للمنظمة: ', '  Organization short name: ')).strip())
        ten = next((x for x in tenants if x.slug == slug), None)
        if ten is None:
            die(t(f'منظمة غير معروفة: {slug}', f'Unknown organization: {slug}'))
        with tenant_context(ten.id), open(path, 'rb') as fh:
            rep = import_workbook(fh.read())
    print(t(f'\n  ✓ أقسام: {rep["departments"]}، نماذج: {rep["forms"]}، ردود: {rep["responses"]}، '
            f'موظفون: {rep["employees"]}، عهد: {rep["custody"]}، تُرك (موجود): {rep["skipped"]}',
            f'\n  ✓ departments {rep["departments"]}, forms {rep["forms"]}, responses {rep["responses"]}, '
            f'employees {rep["employees"]}, custody {rep["custody"]}, skipped {rep["skipped"]}'))
    for p in rep['problems'][:30]:
        print('   - ' + p)


if __name__ == '__main__':
    main()
