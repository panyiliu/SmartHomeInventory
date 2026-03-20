from __future__ import annotations

from flask import Blueprint, jsonify

from ...services.ai_job_service import ai_job_service

bp = Blueprint("ai_jobs_api", __name__)


@bp.get("/api/ai/jobs/<string:job_id>")
def ai_job_status(job_id: str):
    job = ai_job_service.get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "job 不存在或已过期"}), 404

    payload = {
        "ok": True,
        "job_id": job.job_id,
        "kind": job.kind,
        "status": job.status,
        "duration_ms": job.duration_ms,
        "meta": job.meta or {},
    }

    if job.status == "success":
        payload["result"] = job.result
    elif job.status == "error":
        payload["error"] = job.error or "unknown error"

    return jsonify(payload)

