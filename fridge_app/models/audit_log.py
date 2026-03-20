from __future__ import annotations

from datetime import datetime

from ..extensions import db


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, nullable=True)
    actor_username = db.Column(db.String(64), nullable=True)
    action_type = db.Column(db.String(80), nullable=False)

    target_user_id = db.Column(db.Integer, nullable=True)
    target_username = db.Column(db.String(64), nullable=True)

    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(220), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    meta_json = db.Column(db.Text, nullable=True)  # JSON string (best-effort)

