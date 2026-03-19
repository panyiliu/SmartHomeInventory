from __future__ import annotations

import json
from typing import Any

from ..services.settings_service import get_setting


DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com"
DEFAULT_API_TYPE = "responses"  # "responses" | "chat_completions"
DEFAULT_MODEL = "doubao-seed-2-0-pro-260215"


def _normalize_api_type(v: str | None) -> str:
    s = (v or "").strip().lower()
    if not s:
        return DEFAULT_API_TYPE
    if s in {"responses", "resp"}:
        return "responses"
    if s in {"chat_completions", "chatcompletions", "chat-completions", "chat"}:
        return "chat_completions"
    if s in {"images_generations", "imagesgenerations", "image_generation", "image"}:
        return "images_generations"
    if s in {"contents_generations_tasks", "contentsgenerationstasks", "video_task", "video"}:
        return "contents_generations_tasks"
    # fallback: keep as-is if user passes a value we don't recognize, but don't break.
    return s


def _default_profiles() -> list[dict[str, Any]]:
    # NOTE: these are "templates" for the settings UI. Only some capabilities are
    # currently wired in the app code (vision/text/recipes). Others are prepared
    # for future integration.
    return [
        {
            "name": "vision_responses_pro-260215",
            "api_type": "responses",
            "base_url": DEFAULT_BASE_URL,
            "model": "doubao-seed-2-0-pro-260215",
        },
        {
            "name": "vision_responses_lite-260215",
            "api_type": "responses",
            "base_url": DEFAULT_BASE_URL,
            "model": "doubao-seed-2-0-lite-260215",
        },
        {
            "name": "vision_responses_mini-260215",
            "api_type": "responses",
            "base_url": DEFAULT_BASE_URL,
            "model": "doubao-seed-2-0-mini-260215",
        },
        {
            "name": "vision_chat_code-preview-260215",
            "api_type": "chat_completions",
            "base_url": DEFAULT_BASE_URL,
            "model": "doubao-seed-2-0-code-preview-260215",
        },
        {
            "name": "image_generation_seedream-5",
            "api_type": "images_generations",
            "base_url": DEFAULT_BASE_URL,
            "model": "doubao-seedream-5-0-260128",
            "extra_params": {
                "prompt": "星际穿越，黑洞，黑洞里冲出一辆快支离破碎的复古列车，抢视觉冲击力，电影大片，末日既视感，动感，对比色，oc渲染，光线追踪，动态模糊，景深，超现实主义，深蓝，画面通过细腻的丰富的色彩层次塑造主体与场景，质感真实，暗黑风背景的光影效果营造出氛围，整体兼具艺术幻想感，夸张的广角透视效果，耀光，反射，极致的光影，强引力，吞噬",
                "sequential_image_generation": "disabled",
                "response_format": "url",
                "size": "2K",
                "stream": False,
                "watermark": True,
            },
        },
        {
            "name": "video_task_seedace-i2v",
            "api_type": "contents_generations_tasks",
            "base_url": DEFAULT_BASE_URL,
            "model": "doubao-seedance-1-0-pro-250528",
            "extra_params": {
                "content": [
                    {
                        "type": "text",
                        "text": "无人机以极快速度穿越复杂障碍或自然奇观，带来沉浸式飞行体验  --resolution 1080p  --duration 5 --camerafixed false --watermark true",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_i2v.png"
                        },
                    },
                ]
            },
        },
    ]


def load_ark_profiles() -> list[dict[str, Any]]:
    raw = (get_setting("ark_profiles", "").strip()) or ""
    defaults = _default_profiles()
    if not raw:
        return defaults
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return defaults
        out: list[dict[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            # minimal normalization so later logic can rely on some keys existing
            name = str(item.get("name") or "").strip()
            api_type = _normalize_api_type(item.get("api_type"))
            base_url = str(item.get("base_url") or "").strip() or DEFAULT_BASE_URL
            model = str(item.get("model") or "").strip() or DEFAULT_MODEL
            out.append(
                {
                    **item,
                    "name": name or "profile",
                    "api_type": api_type,
                    "base_url": base_url,
                    "model": model,
                }
            )
        # keep `out` and merge defaults after try-block
        pass
    except Exception:
        return defaults

    # merge defaults (by name) so new template profiles appear automatically
    # for existing users who already saved custom configurations.
    try:
        seen = {str(p.get("name") or "").strip() for p in out}
        for d in defaults:
            dn = str(d.get("name") or "").strip()
            if dn and dn not in seen:
                out.append(d)
    except Exception:
        pass

    return out if out else defaults


def get_active_ark_profile() -> dict[str, Any]:
    profiles = load_ark_profiles()
    active_name = (get_setting("ark_active_profile", "default") or "").strip()

    for p in profiles:
        if str(p.get("name") or "").strip() == active_name:
            return p

    # fallback to first profile to avoid runtime errors
    return profiles[0] if profiles else _default_profiles()[0]


def get_active_ark_profile_for(capability: str) -> dict[str, Any]:
    """
    capability examples:
      - vision_recognize (image -> food list)
      - text_extract (text -> item list)
      - recipes_generate (-> recipes)
      - image_generation (text/image -> image)
      - video_task (text+image -> i2v task)

    Priority:
      1) ark_active_profile_<capability>
      2) legacy ark_active_profile
      3) first profile
    """
    profiles = load_ark_profiles()
    cap = (capability or "").strip()
    setting_key = f"ark_active_profile_{cap}"
    active_name = (get_setting(setting_key, "").strip()) or (get_setting("ark_active_profile", "default").strip())

    for p in profiles:
        if str(p.get("name") or "").strip() == active_name:
            return p
    return profiles[0] if profiles else _default_profiles()[0]


def build_ark_endpoint(profile: dict[str, Any]) -> str:
    """
    Return full endpoint URL.
    Supports either:
      - base_url like "https://ark.xxx.com" (we append /api/v3/xxx)
      - full endpoint url directly (we keep it)
    """
    endpoint = str(profile.get("endpoint") or "").strip()
    if endpoint:
        return endpoint

    base_url = str(profile.get("base_url") or "").strip().rstrip("/")
    api_type = _normalize_api_type(profile.get("api_type"))

    if "/api/v3/responses" in base_url:
        return base_url
    if "/api/v3/chat/completions" in base_url:
        return base_url
    if "/api/v3/images/generations" in base_url:
        return base_url
    if "/api/v3/contents/generations/tasks" in base_url:
        return base_url

    if api_type == "chat_completions":
        return f"{base_url}/api/v3/chat/completions"
    if api_type == "images_generations":
        return f"{base_url}/api/v3/images/generations"
    if api_type == "contents_generations_tasks":
        return f"{base_url}/api/v3/contents/generations/tasks"
    # default: responses
    return f"{base_url}/api/v3/responses"


def merged_request_params(profile: dict[str, Any]) -> dict[str, Any]:
    extra = profile.get("extra_params")
    out: dict[str, Any] = {}
    if isinstance(extra, dict):
        out.update(extra)

    # convenience fields (optional)
    for k in ["reasoning_effort", "temperature", "top_p", "max_tokens", "stream"]:
        if k in profile and k not in out:
            out[k] = profile[k]
    return out


def get_timeout_seconds(profile: dict[str, Any], default: float = 60) -> float:
    try:
        v = profile.get("timeout_s", default)
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)

