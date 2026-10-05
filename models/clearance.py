"""براءة الذمة — mirrors the النماذج and الردود sheets of the Apps Script.

Sheet النماذج columns: formId, formCode, empCode, employeeName, employeeId,
employeeEmail, department, resignDate, createdAt, status, hrNote.
Sheet الردود columns: formId, deptId, status, note, respondedAt, respondedBy.
Every sheet column has a home here; the *_raw columns keep the original
text from the sheet (e.g. Hijri dates written by toLocaleDateString('ar-SA')).
"""
from extensions import db
from tenancy import TenantScoped
from models.core import utcnow

# Stored keys -> the exact Arabic labels the script used
FORM_STATUS = {
    'in_progress': 'قيد التنفيذ',
    'complete': 'مكتمل',
    'closed': 'مقفل',
    'cancelled': 'ملغي',
}
RESPONSE_STATUS = {
    'pending': 'قيد الانتظار',
    'cleared': 'خالية الذمة',
    'held': 'معلق',
}
FORM_STATUS_FROM_AR = {v: k for k, v in FORM_STATUS.items()}
RESPONSE_STATUS_FROM_AR = {v: k for k, v in RESPONSE_STATUS.items()}


class ClearanceForm(TenantScoped, db.Model):
    __tablename__ = 'clearance_forms'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'code', name='uq_clearance_tenant_code'),
        db.Index('ix_clearance_tenant_status', 'tenant_id', 'status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    legacy_id = db.Column(db.String(50))                 # FORM-XXXXXX from the sheet
    code = db.Column(db.String(40), nullable=False)      # CLR-2026-XXXXXX
    emp_code = db.Column(db.String(40))                  # EMP-XXXXXX
    employee_name = db.Column(db.String(200), nullable=False)
    employee_number = db.Column(db.String(50))
    employee_email = db.Column(db.String(200))
    employee_department = db.Column(db.String(200))
    employee_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    resign_date = db.Column(db.Date)
    resign_date_raw = db.Column(db.String(60))
    status = db.Column(db.String(20), default='in_progress', nullable=False)
    hr_note = db.Column(db.Text)                         # "إرسال للمحاسبة" note
    finance_note = db.Column(db.Text)                    # settlement note (kept separate)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    created_at_raw = db.Column(db.String(60))
    completed_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    settled_at = db.Column(db.DateTime)
    settled_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    cancelled_at = db.Column(db.DateTime)

    responses = db.relationship('ClearanceResponse', back_populates='form', lazy='selectin',
                                cascade='all, delete-orphan',
                                order_by='ClearanceResponse.sort_order')

    @property
    def status_label(self):
        return FORM_STATUS.get(self.status, self.status)

    @property
    def progress(self):
        total = len(self.responses)
        done = sum(1 for r in self.responses if r.status != 'pending')
        return round(done * 100 / total) if total else 0

    @property
    def held_responses(self):
        return [r for r in self.responses if r.status == 'held']

    @property
    def has_held(self):
        return any(r.status == 'held' for r in self.responses)

    @property
    def is_open(self):
        return self.status not in ('closed', 'cancelled')

    @property
    def is_settled(self):
        """Same rule as printForm(): closed while at least one dept is held."""
        return self.status == 'closed' and self.has_held


class ClearanceResponse(TenantScoped, db.Model):
    __tablename__ = 'clearance_responses'
    __table_args__ = (
        db.UniqueConstraint('form_id', 'department_id', name='uq_response_form_dept'),
    )
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('clearance_forms.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'),
                              index=True)
    department_name = db.Column(db.String(200))   # snapshot; script showed 'محذوف' when missing
    department_icon = db.Column(db.String(20))
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False)
    note = db.Column(db.Text)
    responded_at = db.Column(db.DateTime)
    responded_at_raw = db.Column(db.String(60))
    responded_by = db.Column(db.String(200))
    responded_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))

    form = db.relationship('ClearanceForm', back_populates='responses')
    department = db.relationship('Department', lazy='joined')

    @property
    def status_label(self):
        return RESPONSE_STATUS.get(self.status, self.status)

    @property
    def name(self):
        return self.department.name if self.department else (self.department_name or 'محذوف')

    @property
    def icon(self):
        return self.department.icon if self.department else (self.department_icon or '❓')


class ActionToken(TenantScoped, db.Model):
    """One-time secret for links sent by email (e.g. a department's sign
    link). Only the SHA-256 is stored. The link still requires the holder
    to be logged in with access to that department; the token pins it to
    one form/department and dies once that department responds."""
    __tablename__ = 'action_tokens'
    id = db.Column(db.Integer, primary_key=True)
    purpose = db.Column(db.String(40), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    form_id = db.Column(db.Integer, db.ForeignKey('clearance_forms.id', ondelete='CASCADE'))
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='CASCADE'))
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
