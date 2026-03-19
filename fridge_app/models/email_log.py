from __future__ import annotations

from datetime import datetime

from ..extensions import db


class EmailLog(db.Model):
    __tablename__ = "email_logs"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    to_emails = db.Column(db.String(2000), nullable=False, default="")
    subject = db.Column(db.String(300), nullable=False, default="")
    status = db.Column(db.String(30), nullable=False, default="sent")
    error = db.Column(db.String(2000), nullable=False, default="")

