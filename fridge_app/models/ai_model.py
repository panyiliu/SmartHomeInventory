from __future__ import annotations

from datetime import datetime

from ..extensions import db


class AiModel(db.Model):
    __tablename__ = "ai_models"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    # Friendly display name for UI (e.g. 豆包-Code / 豆包-Pro / GLM-4-7)
    display_name = db.Column(db.String(160), nullable=False, default="")
    api_type = db.Column(db.String(60), nullable=False, default="responses")
    base_url = db.Column(db.String(500), nullable=False, default="https://ark.cn-beijing.volces.com")
    model_name = db.Column(db.String(120), nullable=False, default="")

    enabled = db.Column(db.Boolean, nullable=False, default=True)

    # Stored as JSON text (user-defined free-form payload template).
    # Placeholders supported for test:
    #   {{model}}  -> model_name
    #   {{prompt}} -> selected prompt content
    #   {{user_text}} -> test text input
    #   {{image_data_url}} -> uploaded image data url (vision tests)
    request_template = db.Column(db.Text, nullable=True, default="")

    # Response handling.
    response_parse_mode = db.Column(db.String(60), nullable=False, default="auto_json")  # auto_json | raw
    response_success_contains = db.Column(db.String(500), nullable=False, default="")

    headers_extra_json = db.Column(db.Text, nullable=True, default="{}")  # extra headers only
    timeout_s = db.Column(db.Integer, nullable=False, default=60)

    # Which app capabilities this engine supports.
    # Stored as JSON array string, e.g. ["vision_recognize","text_extract"].
    capabilities_json = db.Column(db.Text, nullable=True, default="[]")

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

