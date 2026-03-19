from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint

from ..extensions import db


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), nullable=False, default="其他")
    quantity = db.Column(db.Float, nullable=False, default=0.0)
    unit = db.Column(db.String(50), nullable=False, default="份")
    location = db.Column(db.String(100), nullable=False, default="冰箱")
    note = db.Column(db.String(500), nullable=False, default="")
    barcode = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    shelf_life_days = db.Column(db.Integer, nullable=True)
    used_up = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_items_quantity_nonnegative"),)

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()

    def stored_days(self, now: datetime | None = None) -> int:
        now = now or datetime.utcnow()
        delta = now.date() - self.created_at.date()
        return delta.days if delta.days >= 0 else 0

    def remaining_days(self, now: datetime | None = None) -> int | None:
        if self.shelf_life_days is None:
            return None
        return int(self.shelf_life_days) - self.stored_days(now=now)

