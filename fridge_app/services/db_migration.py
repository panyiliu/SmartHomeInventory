from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import OperationalError


def ensure_schema(db: SQLAlchemy) -> None:
    """
    Lightweight schema migration for SQLite (no Alembic).
    Adds new columns if an existing database was created before.
    """
    try:
        cols = {row[1] for row in db.session.execute(db.text("PRAGMA table_info(items)")).all()}
    except OperationalError:
        return

    if "created_at" not in cols:
        db.session.execute(db.text("ALTER TABLE items ADD COLUMN created_at DATETIME"))
        db.session.execute(
            db.text("UPDATE items SET created_at = COALESCE(created_at, updated_at, CURRENT_TIMESTAMP)")
        )
    if "shelf_life_days" not in cols:
        db.session.execute(db.text("ALTER TABLE items ADD COLUMN shelf_life_days INTEGER"))
    if "archived" not in cols:
        db.session.execute(db.text("ALTER TABLE items ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"))
    if "used_up" not in cols:
        db.session.execute(db.text("ALTER TABLE items ADD COLUMN used_up BOOLEAN NOT NULL DEFAULT 0"))
    if "quick_step" not in cols:
        db.session.execute(db.text("ALTER TABLE items ADD COLUMN quick_step FLOAT"))
    if "barcode" not in cols:
        db.session.execute(db.text("ALTER TABLE items ADD COLUMN barcode VARCHAR(64)"))

    # ai_models table lightweight migration (new columns)
    try:
        ai_model_cols = {row[1] for row in db.session.execute(db.text("PRAGMA table_info(ai_models)")).all()}
    except OperationalError:
        ai_model_cols = set()

    if ai_model_cols:
        if "display_name" not in ai_model_cols:
            db.session.execute(db.text("ALTER TABLE ai_models ADD COLUMN display_name VARCHAR(160) NOT NULL DEFAULT ''"))
        if "enabled" not in ai_model_cols:
            db.session.execute(db.text("ALTER TABLE ai_models ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 1"))
        if "timeout_s" not in ai_model_cols:
            db.session.execute(db.text("ALTER TABLE ai_models ADD COLUMN timeout_s INTEGER NOT NULL DEFAULT 60"))
        if "capabilities_json" not in ai_model_cols:
            db.session.execute(db.text("ALTER TABLE ai_models ADD COLUMN capabilities_json TEXT DEFAULT '[]'"))

    db.session.commit()

