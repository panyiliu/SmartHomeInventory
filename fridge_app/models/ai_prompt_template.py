from __future__ import annotations

from datetime import datetime

from ..extensions import db


class AiPromptTemplate(db.Model):
    __tablename__ = "ai_prompt_templates"

    id = db.Column(db.Integer, primary_key=True)

    # Category code, e.g. vision_recognize / text_extract / interface_test
    category_code = db.Column(db.String(80), nullable=False, index=True)

    name = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False, default="")

    # Only one default per category_code (enforced in app logic)
    is_default = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

