from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable


@dataclass
class AiJob:
    job_id: str
    kind: str
    status: str  # running | success | error
    created_at: float
    updated_at: float
    meta: dict[str, Any]
    result: Any = None
    error: str | None = None
    duration_ms: int | None = None


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _json_loads(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


class AiJobService:
    """
    AI 异步任务：带 `app` 时写入数据库，便于 gunicorn 多 worker 下轮询命中任意进程。

    无 `app` 时仍使用进程内内存（测试或极简场景）。
    """

    def __init__(self, *, ttl_s: int = 3600):
        self.ttl_s = int(ttl_s)
        self._lock = threading.Lock()
        self._jobs: dict[str, AiJob] = {}
        self._last_db_cleanup = 0.0

    def _cleanup_locked(self) -> None:
        if self.ttl_s <= 0:
            return
        now = time.time()
        expired: list[str] = []
        for jid, job in self._jobs.items():
            if job.status == "running":
                continue
            if now - job.updated_at > self.ttl_s:
                expired.append(jid)
        for jid in expired:
            self._jobs.pop(jid, None)

    def _maybe_cleanup_db_jobs(self) -> None:
        if self.ttl_s <= 0:
            return
        now = time.time()
        if now - self._last_db_cleanup < 60.0:
            return
        self._last_db_cleanup = now
        from ..extensions import db
        from ..models.ai_async_job import AiAsyncJob as AiAsyncJobRow

        cutoff = datetime.utcnow() - timedelta(seconds=self.ttl_s)
        try:
            AiAsyncJobRow.query.filter(
                AiAsyncJobRow.status != "running",
                AiAsyncJobRow.updated_at < cutoff,
            ).delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()

    def _persist_job_row(
        self,
        *,
        job_id: str,
        kind: str,
        status: str,
        meta: dict[str, Any],
        result: Any = None,
        error: str | None = None,
        duration_ms: int | None = None,
        commit: bool = True,
    ) -> None:
        from ..extensions import db
        from ..models.ai_async_job import AiAsyncJob as AiAsyncJobRow

        now = datetime.utcnow()
        row = db.session.get(AiAsyncJobRow, job_id)
        if row is None:
            row = AiAsyncJobRow(
                job_id=job_id,
                kind=kind,
                status=status,
                meta_json=_json_dumps(meta),
                created_at=now,
                updated_at=now,
            )
            db.session.add(row)
        else:
            row.kind = kind
            row.status = status
            row.meta_json = _json_dumps(meta)
            row.updated_at = now

        if status == "success":
            row.result_json = _json_dumps(result)
            row.error = None
        elif status == "error":
            row.result_json = None
            row.error = error or "AI job error"
        else:
            row.result_json = None
            row.error = None

        row.duration_ms = duration_ms
        if commit:
            db.session.commit()

    def _row_to_aijob(self, row: Any) -> AiJob | None:
        if row.status != "running" and self.ttl_s > 0:
            age = (datetime.utcnow() - row.updated_at).total_seconds()
            if age > self.ttl_s:
                return None
        meta = _json_loads(row.meta_json, {})
        if not isinstance(meta, dict):
            meta = {}
        result = None
        if row.status == "success" and row.result_json:
            result = _json_loads(row.result_json, None)
        return AiJob(
            job_id=row.job_id,
            kind=row.kind,
            status=row.status,
            created_at=row.created_at.timestamp(),
            updated_at=row.updated_at.timestamp(),
            meta=meta,
            result=result,
            error=row.error,
            duration_ms=row.duration_ms,
        )

    def _get_job_from_db(self, job_id: str) -> AiJob | None:
        from ..extensions import db
        from ..models.ai_async_job import AiAsyncJob as AiAsyncJobRow

        self._maybe_cleanup_db_jobs()
        row = db.session.get(AiAsyncJobRow, job_id)
        if row is None:
            return None
        return self._row_to_aijob(row)

    def create_job(
        self,
        *,
        kind: str,
        fn: Callable[[], Any],
        meta: dict[str, Any] | None = None,
        app: Any | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        meta = meta or {}
        kind_s = str(kind or "unknown")

        if app is not None:
            with app.app_context():
                self._persist_job_row(
                    job_id=job_id,
                    kind=kind_s,
                    status="running",
                    meta=meta,
                    commit=True,
                )

            def _runner_db() -> None:
                start = time.time()
                try:
                    with app.app_context():
                        from ..extensions import db

                        try:
                            result = fn()
                            duration_ms = int((time.time() - start) * 1000)
                            self._persist_job_row(
                                job_id=job_id,
                                kind=kind_s,
                                status="success",
                                meta=meta,
                                result=result,
                                duration_ms=duration_ms,
                                commit=True,
                            )
                        except Exception as e:
                            duration_ms = int((time.time() - start) * 1000)
                            self._persist_job_row(
                                job_id=job_id,
                                kind=kind_s,
                                status="error",
                                meta=meta,
                                error=str(e) or "AI job error",
                                duration_ms=duration_ms,
                                commit=True,
                            )
                        finally:
                            db.session.remove()

                except Exception:
                    # 应用上下文或 DB 异常：尽力标记失败
                    try:
                        with app.app_context():
                            from ..extensions import db

                            duration_ms = int((time.time() - start) * 1000)
                            self._persist_job_row(
                                job_id=job_id,
                                kind=kind_s,
                                status="error",
                                meta=meta,
                                error="任务更新失败（服务器内部错误）",
                                duration_ms=duration_ms,
                                commit=True,
                            )
                            db.session.remove()
                    except Exception:
                        pass

            t = threading.Thread(target=_runner_db, daemon=True)
            t.start()
            return job_id

        # —— 无 app：仅内存（单进程） ——
        now = time.time()
        job = AiJob(
            job_id=job_id,
            kind=kind_s,
            status="running",
            created_at=now,
            updated_at=now,
            meta=meta,
        )
        with self._lock:
            self._cleanup_locked()
            self._jobs[job_id] = job

        def _runner_mem() -> None:
            start = time.time()
            try:
                result = fn()
                duration_ms = int((time.time() - start) * 1000)
                with self._lock:
                    j = self._jobs.get(job_id)
                    if not j:
                        return
                    j.status = "success"
                    j.result = result
                    j.error = None
                    j.duration_ms = duration_ms
                    j.updated_at = time.time()
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                with self._lock:
                    j = self._jobs.get(job_id)
                    if not j:
                        return
                    j.status = "error"
                    j.error = str(e) or "AI job error"
                    j.result = None
                    j.duration_ms = duration_ms
                    j.updated_at = time.time()

        t = threading.Thread(target=_runner_mem, daemon=True)
        t.start()
        return job_id

    def get_job(self, job_id: str) -> AiJob | None:
        if not job_id:
            return None
        try:
            from flask import has_app_context

            if has_app_context():
                return self._get_job_from_db(job_id)
        except Exception:
            pass
        with self._lock:
            self._cleanup_locked()
            return self._jobs.get(job_id)


# Singleton for the whole app process（DB 行全进程共享）。
ai_job_service = AiJobService()
