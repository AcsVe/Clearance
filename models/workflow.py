"""Employees roster, clearance forms (one response per department) and custody (عهد).
Mirrors the original sheets «النماذج», «الردود» and «العهد», with a proper employees table added."""
import secrets

from extensions import db
from tenancy import TenantScoped
from .core import utcnow


def new_token():
    return secrets.token_urlsafe(32)


class Employee(TenantScoped, db.Model):
    """The staff roster (like the teachers list in the tickets system). A clearance form or a custody
    item can point to an employee; an employee may also have a login (user_id) but does not need one."""
    __tablename__ = 'employees'
    __table_args__ = (db.UniqueConstraint('tenant_id', 'employee_number', name='uq_employees_tenant_number'),)
    id = db.Column(db.Integer, primary_key=True)
    employee_number = db.Column(db.String(50))
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    job_title = db.Column(db.String(150))
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    hire_date = db.Column(db.Date)
    notes = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    department = db.relationship('Department', lazy='joined')
    user = db.relationship('User', lazy='joined')


FORM_STATUSES = [('in_progress', 'قيد التنفيذ'), ('completed', 'مكتمل'), ('closed', 'مقفل'), ('cancelled', 'ملغي')]
RESPONSE_STATUSES = [('pending', 'قيد الانتظار'), ('cleared', 'خالية الذمة'), ('held', 'معلق')]
CUSTODY_STATUSES = [('handed', 'مسلمة'), ('returned', 'مسترجعة')]


class ClearanceForm(TenantScoped, db.Model):
    """«النماذج». The employee's details are copied onto the form so it stays a faithful record even if
    the roster changes later."""
    __tablename__ = 'clearance_forms'
    __table_args__ = (db.UniqueConstraint('tenant_id', 'ref_code', name='uq_clearance_tenant_ref'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_id = db.Column(db.String(50))                    # FORM-xxxxxx from the sheet
    ref_code = db.Column(db.String(50), nullable=False)     # CLR-YYYY-xxxxxx
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id', ondelete='SET NULL'))
    employee_number = db.Column(db.String(50))
    employee_name = db.Column(db.String(200), nullable=False)
    employee_email = db.Column(db.String(200))
    department_name = db.Column(db.String(200))
    job_title = db.Column(db.String(150))
    resign_date = db.Column(db.Date)
    status = db.Column(db.String(20), default='in_progress', nullable=False, index=True)
    hr_note = db.Column(db.String(1000))
    finance_note = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    created_by = db.Column(db.String(200))
    completed_at = db.Column(db.DateTime)
    sent_to_finance_at = db.Column(db.DateTime)
    settled_at = db.Column(db.DateTime)
    settled_by = db.Column(db.String(200))
    closed_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    last_reminder_at = db.Column(db.DateTime)

    employee = db.relationship('Employee', lazy='joined')
    responses = db.relationship('ClearanceResponse', back_populates='form', cascade='all, delete-orphan',
                                lazy='selectin', order_by='ClearanceResponse.id')

    @property
    def status_label(self):
        return dict(FORM_STATUSES).get(self.status, self.status)

    @property
    def is_open(self):
        return self.status in ('in_progress', 'completed')

    @property
    def held(self):
        return [r for r in self.responses if r.status == 'held']

    @property
    def pending(self):
        return [r for r in self.responses if r.status == 'pending']

    @property
    def settled(self):
        return self.settled_at is not None


class ClearanceResponse(TenantScoped, db.Model):
    """«الردود»: one row per department per form. ``token`` is the department's signing link."""
    __tablename__ = 'clearance_responses'
    __table_args__ = (db.UniqueConstraint('form_id', 'department_id', name='uq_response_form_dept'),)
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('clearance_forms.id', ondelete='CASCADE'), nullable=False,
                        index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False, index=True)
    status = db.Column(db.String(20), default='pending', nullable=False)
    note = db.Column(db.String(1000))
    responded_at = db.Column(db.DateTime)
    responded_by = db.Column(db.String(200))
    responded_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    token = db.Column(db.String(64), unique=True, nullable=False, default=new_token)

    form = db.relationship('ClearanceForm', back_populates='responses')
    department = db.relationship('Department', lazy='joined')

    @property
    def status_label(self):
        return dict(RESPONSE_STATUSES).get(self.status, self.status)


class Custody(TenantScoped, db.Model):
    """«العهد»: an item handed to someone by a department, later returned."""
    __tablename__ = 'custodies'
    __table_args__ = (db.UniqueConstraint('tenant_id', 'ref_code', name='uq_custody_tenant_ref'),)
    id = db.Column(db.Integer, primary_key=True)
    ref_code = db.Column(db.String(50), nullable=False)     # CUST-xxxxxx
    item = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    serial_no = db.Column(db.String(100))
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id', ondelete='SET NULL'), index=True)
    recipient_name = db.Column(db.String(200), nullable=False)
    recipient_email = db.Column(db.String(200))
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False, index=True)
    handed_by = db.Column(db.String(200))
    handed_by_email = db.Column(db.String(200))
    handed_at = db.Column(db.Date)
    status = db.Column(db.String(20), default='handed', nullable=False, index=True)
    returned_at = db.Column(db.Date)
    returned_to = db.Column(db.String(200))
    notes = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    department = db.relationship('Department', lazy='joined')
    employee = db.relationship('Employee', lazy='joined')

    @property
    def status_label(self):
        return dict(CUSTODY_STATUSES).get(self.status, self.status)
