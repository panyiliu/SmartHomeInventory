from __future__ import annotations

from datetime import datetime

from ..extensions import db


class AiAsyncJob(db.Model):
    """
    持久化的 AI 异步任务状态（供多 worker / 多进程下轮询同一 job_id）。
    """

    __tablename__ = "ai_async_jobs"

    job_id = db.Column(db.String(64), primary_key=True)
    kind = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False)  # running | success | error
    meta_json = db.Column(db.Text, nullable=False, default="{}")
    result_json = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
