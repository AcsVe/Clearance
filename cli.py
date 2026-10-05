"""Management commands. Passwords are always typed into a hidden prompt on
the server, never passed as command-line arguments or pasted elsewhere."""
import getpass
import os

import click

from extensions import db
from tenancy import tenant_bypass


def _prompt_password(username):
    from security import password_problems
    while True:
        pw = getpass.getpass('كلمة المرور (لن تظهر أثناء الكتابة): ')
        pw2 = getpass.getpass('أعد كتابتها: ')
        problems = password_problems(pw, username)
        if pw != pw2:
            problems.append('غير متطابقتين')
        if not problems:
            return pw
        for p in problems:
            click.echo('  - ' + p)


def register_cli(app):
    @app.cli.command('create-superadmin')
    @click.argument('username')
    @click.option('--email', default=None)
    def create_superadmin(username, email):
        """Create a platform super admin (asks for the password)."""
        from models import SuperAdmin
        from security import hash_password
        with tenant_bypass():
            if SuperAdmin.query.filter_by(username=username).first():
                raise click.ClickException('موجود مسبقًا')
            pw = _prompt_password(username)
            db.session.add(SuperAdmin(username=username, email=email,
                                      password_hash=hash_password(pw)))
            db.session.commit()
        click.echo('تم إنشاء المدير العام')

    @app.cli.command('seed-roles')
    @click.argument('slug')
    def seed_roles(slug):
        """Add any missing default roles to an organization."""
        from models import Tenant
        from tenancy import tenant_context
        from authz import seed_default_roles
        t = Tenant.query.filter_by(slug=slug).first()
        if not t:
            raise click.ClickException('منظمة غير موجودة')
        with tenant_context(t.id):
            seed_default_roles(t.id)
            db.session.commit()
        click.echo('تم')

    @app.cli.command('import-sheets')
    @click.argument('slug')
    @click.option('--dir', 'folder', required=True, type=click.Path(exists=True, file_okay=False),
                  help='مجلد ملفات CSV المصدّرة من Google Sheets')
    @click.option('--commit', is_flag=True, help='الكتابة فعليًا (بدونها: تقرير فقط)')
    @click.option('--date-order', type=click.Choice(['dmy', 'mdy']), default='dmy',
                  help='ترتيب اليوم/الشهر في التواريخ الميلادية الملتبسة')
    @click.option('--report', 'report_path', default=None, help='حفظ التقرير في ملف')
    def import_sheets(slug, folder, commit, date_order, report_path):
        """Import الاقسام / النماذج / الردود / العهد from CSV exports."""
        from models import Tenant
        from tenancy import tenant_context
        from importers.sheets import find_sheet_files, SheetImporter
        t = Tenant.query.filter_by(slug=slug).first()
        if not t:
            raise click.ClickException('منظمة غير موجودة')
        files = find_sheet_files(folder)
        click.echo('الملفات: ' + ', '.join(f'{k}={os.path.basename(v)}' for k, v in files.items()))
        with tenant_context(t.id):
            imp = SheetImporter(t, files, date_order, app.config.get('TIMEZONE', 'Asia/Amman'))
            try:
                report = imp.run()
            except Exception:
                db.session.rollback()
                raise
            text = report.text()
            if commit:
                db.session.commit()
                text += '\n\n✅ تم الحفظ.'
            else:
                db.session.rollback()
                text += '\n\n(تشغيل تجريبي — لم يُحفظ شيء. أعد التشغيل مع --commit للحفظ)'
        click.echo(text)
        if report_path:
            with open(report_path, 'w', encoding='utf-8') as fh:
                fh.write(text)

    @app.cli.command('fetch-drive-logos')
    @click.argument('slug')
    def fetch_logos(slug):
        """Download the two logos used by the old print pages from Google Drive."""
        from models import Tenant
        from importers.sheets import fetch_drive_logos
        t = Tenant.query.filter_by(slug=slug).first()
        if not t:
            raise click.ClickException('منظمة غير موجودة')
        for m in fetch_drive_logos(t, app.config['UPLOAD_DIR']):
            click.echo(m)
        db.session.commit()

    @app.cli.command('generate-vapid')
    def generate_vapid():
        """Print a new VAPID key pair for Web Push (paste into config.env on the server)."""
        import base64
        from py_vapid import Vapid01
        from cryptography.hazmat.primitives import serialization
        v = Vapid01()
        v.generate_keys()
        pub = v.public_key.public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
        priv = v.private_key.private_numbers().private_value.to_bytes(32, 'big')
        b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip('=')
        click.echo('أضف السطرين التاليين إلى config.env على الخادم ثم أعد تشغيل الخدمة:')
        click.echo(f'VAPID_PUBLIC_KEY={b64(pub)}')
        click.echo(f'VAPID_PRIVATE_KEY={b64(priv)}')
