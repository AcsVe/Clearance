"""In-app notifications and optional Web Push subscriptions."""
from extensions import db
from tenancy import TenantScoped
from models.core import utcnow


class Notification(TenantScoped, db.Model):
    __tablename__ = 'notifications'
    __table_args__ = (db.Index('ix_notif_user_unread', 'tenant_id', 'user_id', 'read_at'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    kind = db.Column(db.String(50))
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.String(500))
    link = db.Column(db.String(300))          # app-relative path only
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


class PushSubscription(TenantScoped, db.Model):
    __tablename__ = 'push_subscriptions'
    __table_args__ = (db.UniqueConstraint('tenant_id', 'endpoint', name='uq_push_endpoint'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
                        index=True)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def to_push_dict(self):
        return {'endpoint': self.endpoint, 'keys': {'p256dh': self.p256dh, 'auth': self.auth}}
