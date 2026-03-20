from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
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


class AiJobService:
    """
    Lightweight in-memory job store.

    Goal:
    - front-end can persist `job_id` in localStorage
    - resume polling across navigation / visibility changes

    Note:
    - in-memory jobs are lost on server restart (acceptable for MVP).
    """

    def __init__(self, *, ttl_s: int = 3600):
        self.ttl_s = int(ttl_s)
        self._lock = threading.Lock()
        self._jobs: dict[str, AiJob] = {}

    def _cleanup_locked(self) -> None:
        if self.ttl_s <= 0:
            return
        now = time.time()
        expired: list[str] = []
        for jid, job in self._jobs.items():
            # Keep running jobs forever until finished; only expire finished.
            if job.status == "running":
                continue
            if now - job.updated_at > self.ttl_s:
                expired.append(jid)
        for jid in expired:
            self._jobs.pop(jid, None)

    def create_job(
        self,
        *,
        kind: str,
        fn: Callable[[], Any],
        meta: dict[str, Any] | None = None,
        app: Any | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        now = time.time()
        job = AiJob(
            job_id=job_id,
            kind=str(kind or "unknown"),
            status="running",
            created_at=now,
            updated_at=now,
            meta=meta or {},
        )

        with self._lock:
            self._cleanup_locked()
            self._jobs[job_id] = job

        def _runner() -> None:
            start = time.time()
            try:
                if app is not None:
                    # Ensure Flask application context for DB/session usage.
                    with app.app_context():
                        result = fn()
                else:
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

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        return job_id

    def get_job(self, job_id: str) -> AiJob | None:
        if not job_id:
            return None
        with self._lock:
            self._cleanup_locked()
            return self._jobs.get(job_id)


# Singleton for the whole app process.
ai_job_service = AiJobService()

