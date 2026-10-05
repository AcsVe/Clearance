"""العهد العينية — mirrors the العهد sheet:
معرف_العهدة, العهدة, المستلم, بريد_المستلم, القسم, كود_القسم, مسلم_العهدة,
تاريخ_التسليم, الحالة, تاريخ_الإرجاع, مستلم_الإرجاع, بريد_مسلم_العهدة
+ an unnamed 13th column where returnCustody() wrote the returner's email.
"""
from extensions import db
from tenancy import TenantScoped
from models.core import utcnow

CUSTODY_STATUS = {'handed': 'مسلمة', 'returned': 'مسترجعة'}
CUSTODY_STATUS_FROM_AR = {v: k for k, v in CUSTODY_STATUS.items()}


class Custody(TenantScoped, db.Model):
    __tablename__ = 'custodies'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'code', name='uq_custody_tenant_code'),
        db.Index('ix_custody_tenant_dept_status', 'tenant_id', 'department_id', 'status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False)                # CUST-XXXXXX (sheet id kept)
    item_name = db.Column(db.String(300), nullable=False)
    recipient_name = db.Column(db.String(200), nullable=False)
    recipient_email = db.Column(db.String(200))
    recipient_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'),
                              index=True)
    department_name = db.Column(db.String(200))                    # snapshot (القسم)
    department_code = db.Column(db.String(50))                     # snapshot (كود_القسم)
    handed_by_name = db.Column(db.String(200))
    handed_by_email = db.Column(db.String(200))
    handed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    handed_at = db.Column(db.DateTime)
    handed_at_raw = db.Column(db.String(60))
    expected_return_date = db.Column(db.Date)                      # for overdue reminders
    status = db.Column(db.String(20), default='handed', nullable=False)
    returned_at = db.Column(db.DateTime)
    returned_at_raw = db.Column(db.String(60))
    returned_to_name = db.Column(db.String(200))                   # مستلم_الإرجاع
    returned_to_email = db.Column(db.String(200))                  # unnamed 13th column
    returned_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    notes = db.Column(db.Text)
    source = db.Column(db.String(20), default='manual', nullable=False)  # manual | sheet | ocr
    source_row = db.Column(db.Integer)                             # row number in the sheet
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_reminder_at = db.Column(db.DateTime)

    department = db.relationship('Department', lazy='joined')

    @property
    def status_label(self):
        return CUSTODY_STATUS.get(self.status, self.status)

    @property
    def dept_label(self):
        return self.department.name if self.department else (self.department_name or '—')
