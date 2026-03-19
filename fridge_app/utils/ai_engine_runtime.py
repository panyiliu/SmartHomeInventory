from __future__ import annotations

import base64
import json
import os
from typing import Any

import requests

from ..models import AiModel
from ..services.settings_service import get_secret_setting, get_setting
from .ark_config import build_ark_endpoint
from .ai_parse import extract_json, extract_output_text


def _deep_replace(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _deep_replace(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_replace(v, mapping) for v in value]
    if isinstance(value, str):
        out = value
        for k, v in mapping.items():
            out = out.replace(k, v)
        return out
    return value


def _image_bytes_to_data_url(image_bytes: bytes, *, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    ext = "jpeg"
    if mime == "image/png":
        ext = "png"
    elif mime == "image/webp":
        ext = "webp"
    return f"data:image/{ext};base64,{b64}"


def _get_engine_id(setting_key: str) -> int | None:
    raw = (get_setting(setting_key, "") or "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
        return v if v > 0 else None
    except Exception:
        return None


def _get_engine(setting_key: str) -> AiModel | None:
    mid = _get_engine_id(setting_key)
    if not mid:
        return None
    try:
        row = AiModel.query.get(mid)
        if row and row.enabled:
            return row
        return None
    except Exception:
        return None


def _call_engine(
    engine: AiModel,
    *,
    mapping: dict[str, str],
    force_non_stream: bool = False,
    drop_tools: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        raise RuntimeError("VOLCENGINE_API_KEY missing (set env var or save it in 设置页)")

    template_raw = (engine.request_template or "").strip()
    if not template_raw:
        raise RuntimeError("引擎 request_template 为空")
    try:
        payload_template = json.loads(template_raw)
    except Exception as e:
        raise RuntimeError(f"引擎 request_template JSON 解析失败：{e}")

    payload = _deep_replace(payload_template, {"{{model}}": engine.model_name, **mapping})

    endpoint = build_ark_endpoint({"base_url": engine.base_url, "api_type": engine.api_type})
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    if force_non_stream:
        payload["stream"] = False
    if drop_tools and "tools" in payload:
        payload.pop("tools", None)

    wants_stream = bool(payload.get("stream") is True)

    # Verbose logging (same env vars as other AI paths)
    verbose_raw = (os.environ.get("AI_LOG_VERBOSE") or "1").strip().lower()
    verbose = verbose_raw in {"1", "true", "yes", "on"}
    full_raw = (os.environ.get("AI_LOG_FULL_RESPONSE") or "1").strip().lower()
    full_response = full_raw in {"1", "true", "yes", "on"}

    if verbose:
        try:
            pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception:
            pretty = ""
        print("=" * 80)
        print("[AI][ENGINE_CALL] start")
        print("engine_id:", engine.id)
        print("engine:", engine.display_name or engine.name)
        print("api_type:", engine.api_type, "model:", engine.model_name)
        print("endpoint:", endpoint)
        if pretty:
            print("payload:\n", pretty)
        print("=" * 80)

    resp = requests.post(endpoint, headers=headers, json=payload, timeout=engine.timeout_s, stream=wants_stream)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code} {resp.reason}: {(resp.text or '')[:2000]}")

    # stream: collect deltas (best effort)
    if wants_stream:
        chunks: list[str] = []
        event_count = 0
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            try:
                line = raw_line.decode("utf-8", errors="ignore").strip()
            except Exception:
                continue
            if line == "data: [DONE]":
                break
            if not line.startswith("data: "):
                continue
            s = line[6:].strip()
            try:
                ev = json.loads(s)
            except Exception:
                continue

            if not isinstance(ev, dict):
                continue
            event_count += 1
            if ev.get("type") == "error":
                msg = ev.get("message") or ev.get("error") or "stream error"
                raise RuntimeError(str(msg))
            # common: {"type":"response.output_text.delta","delta":"..."}
            delta = ev.get("delta", "")
            if isinstance(delta, str) and delta:
                chunks.append(delta)
        stream_text = "".join(chunks)
        if verbose:
            print("=" * 80)
            print("[AI][ENGINE_CALL] stream done")
            print("stream_events:", event_count, "stream_chars:", len(stream_text))
            if full_response:
                print("stream_text:\n", stream_text)
            print("=" * 80)
        # Fallback: if stream yields nothing, retry once in non-stream mode.
        if not stream_text:
            payload2 = dict(payload)
            payload2["stream"] = False
            if verbose:
                print("[AI][ENGINE_CALL] stream empty -> retry non-stream once")
            resp2 = requests.post(endpoint, headers=headers, json=payload2, timeout=engine.timeout_s)
            if not resp2.ok:
                raise RuntimeError(f"HTTP {resp2.status_code} {resp2.reason}: {(resp2.text or '')[:2000]}")
            try:
                body2 = resp2.json()
            except Exception:
                body2 = {"raw_text": resp2.text or ""}
            out_text2 = extract_output_text(body2) if isinstance(body2, dict) else ""
            return (body2 if isinstance(body2, dict) else None), out_text2

        # Return stream_text as out_text too, so callers can extract JSON from it.
        return {"stream_text": stream_text}, stream_text

    try:
        body = resp.json()
    except Exception:
        body = {"raw_text": resp.text or ""}

    out_text = extract_output_text(body) if isinstance(body, dict) else ""
    if verbose:
        try:
            body_pretty = json.dumps(body, ensure_ascii=False, indent=2) if isinstance(body, (dict, list)) else str(body)
        except Exception:
            body_pretty = ""
        print("=" * 80)
        print("[AI][ENGINE_CALL] done")
        if full_response and body_pretty:
            print("response_json:\n", body_pretty)
        elif full_response:
            print("response_text:\n", (resp.text or "")[:5000])
        print("=" * 80)
    return (body if isinstance(body, dict) else None), out_text


def vision_recognize_with_engine(image_bytes: bytes, *, prompt: str) -> Any:
    engine = _get_engine("ai_engine_vision_model_id")
    if not engine:
        return None
    if "{{image_data_url}}" not in (engine.request_template or ""):
        # Misconfigured engine for vision; fall back to other path.
        return None
    data_url = _image_bytes_to_data_url(image_bytes)
    body, out_text = _call_engine(engine, mapping={"{{image_data_url}}": data_url, "{{prompt}}": prompt})
    # For non-stream: try parse JSON from output text
    return extract_json(out_text) if out_text else body or {}


def text_extract_with_engine(user_text: str, *, prompt: str) -> Any:
    engine = _get_engine("ai_engine_text_model_id")
    if not engine:
        return None
    if "{{user_text}}" not in (engine.request_template or ""):
        return None
    body, out_text = _call_engine(engine, mapping={"{{user_text}}": user_text, "{{prompt}}": prompt})
    return extract_json(out_text) if out_text else body or {}


def recipes_generate_with_engine(prompt: str, *, user_text: str = "") -> Any:
    engine = _get_engine("ai_engine_recipes_model_id")
    if not engine:
        return None
    if "{{prompt}}" not in (engine.request_template or ""):
        return None
    if "{{user_text}}" in (engine.request_template or "") and not (user_text or "").strip():
        user_text = "请根据以上食材推荐 3~5 道家常晚餐菜谱。"
    # recipes_generate needs stable non-stream JSON output; drop tools/search by default.
    body, out_text = _call_engine(
        engine,
        mapping={"{{prompt}}": prompt, "{{user_text}}": user_text or ""},
        force_non_stream=True,
        drop_tools=True,
    )
    return extract_json(out_text) if out_text else body or {}

