from __future__ import annotations

from datetime import datetime

from ..extensions import db


class Setting(db.Model):
    __tablename__ = "settings"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(2000), nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

