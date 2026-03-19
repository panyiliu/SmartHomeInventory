from __future__ import annotations

from typing import Any

import requests

from ..services.settings_service import get_secret_setting
from .ark_config import build_ark_endpoint, get_active_ark_profile_for, get_timeout_seconds, merged_request_params


def _call_ark(profile: dict[str, Any], payload: dict[str, Any]) -> Any:
    api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        raise RuntimeError("VOLCENGINE_API_KEY missing (set env var or save it in 设置页)")

    endpoint = build_ark_endpoint(profile)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout_s = get_timeout_seconds(profile, default=60)
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def call_image_generation() -> Any:
    """
    image_generation预留能力：
    直接调用 profile 对应的 /api/v3/images/generations，并返回原始 JSON。
    """
    profile = get_active_ark_profile_for("image_generation")
    if str(profile.get("api_type") or "").strip() != "images_generations":
        raise RuntimeError(f"image_generation 仅支持 images_generations，当前={profile.get('api_type')}")

    model = str(profile.get("model") or "")
    extra_params = merged_request_params(profile)
    payload: dict[str, Any] = {"model": model}
    if extra_params:
        payload.update(extra_params)
    return _call_ark(profile, payload)


def create_video_generation_task() -> Any:
    """
    video_task预留能力：
    直接创建 /api/v3/contents/generations/tasks 任务，并返回原始 JSON。
    """
    profile = get_active_ark_profile_for("video_task")
    if str(profile.get("api_type") or "").strip() != "contents_generations_tasks":
        raise RuntimeError(
            f"video_task 仅支持 contents_generations_tasks，当前={profile.get('api_type')}"
        )

    model = str(profile.get("model") or "")
    extra_params = merged_request_params(profile)
    payload: dict[str, Any] = {"model": model}
    if extra_params:
        payload.update(extra_params)
    return _call_ark(profile, payload)

