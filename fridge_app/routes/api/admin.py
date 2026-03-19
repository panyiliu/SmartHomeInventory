from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import requests
from flask import Blueprint, jsonify, request

from ...models import AiModel, AiPromptTemplate
from ...services.settings_service import get_secret_setting
from ...utils.ai_parse import extract_output_text
from ...utils.auth import admin_required


bp = Blueprint("admin_api", __name__, url_prefix="/admin")


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


def _strip_empty_image_blocks(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_empty_image_blocks(v) for k, v in obj.items()}
    if isinstance(obj, list):
        cleaned = []
        for item in obj:
            x = _strip_empty_image_blocks(item)
            if isinstance(x, dict):
                t = str(x.get("type") or "").strip()
                if t == "image_url":
                    iu = x.get("image_url")
                    url = ""
                    if isinstance(iu, dict):
                        url = str(iu.get("url") or "").strip()
                    if not url:
                        continue
                if t == "input_image":
                    url = str(x.get("image_url") or "").strip()
                    if not url:
                        continue
            cleaned.append(x)
        return cleaned
    return obj


@bp.post("/api/ai-engine/test")
@admin_required
def api_ai_engine_test():
    payload = request.get_json(silent=True) or {}
    engine_id = payload.get("engine_id")
    try:
        engine_id_int = int(str(engine_id or "").strip())
    except Exception:
        return jsonify({"ok": False, "error": "engine_id 不合法"}), 400

    engine = AiModel.query.get(engine_id_int)
    if not engine:
        return jsonify({"ok": False, "error": "引擎不存在"}), 404
    if not engine.enabled:
        return jsonify({"ok": False, "error": "引擎已停用"}), 400

    api_key = (os.environ.get("VOLCENGINE_API_KEY") or "").strip()
    if not api_key:
        api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        return jsonify({"ok": False, "error": "未配置 VOLCENGINE_API_KEY"}), 400

    prompt_row = (
        AiPromptTemplate.query.filter_by(category_code="interface_test", is_default=True)
        .order_by(AiPromptTemplate.updated_at.desc())
        .first()
    )
    prompt_text = (prompt_row.content if prompt_row else "") or "你好，请回复“测试成功”。"

    tpl_raw = (engine.request_template or "").strip()
    if not tpl_raw:
        return jsonify({"ok": False, "error": "引擎 request_template 为空"}), 400
    try:
        tpl = json.loads(tpl_raw)
    except Exception as e:
        return jsonify({"ok": False, "error": f"引擎 request_template JSON 解析失败：{e}"}), 400

    mapping = {
        "{{model}}": engine.model_name or "",
        "{{prompt}}": prompt_text,
        "{{user_text}}": "",
        "{{image_data_url}}": "",
    }
    req_body = _strip_empty_image_blocks(_deep_replace(tpl, mapping))

    if isinstance(req_body, dict) and req_body.get("stream") is True:
        req_body["stream"] = False

    base = (engine.base_url or "").strip().rstrip("/")
    if engine.api_type == "chat_completions":
        endpoint = f"{base}/api/v3/chat/completions"
    elif engine.api_type == "images_generations":
        endpoint = f"{base}/api/v3/images/generations"
    elif engine.api_type == "contents_generations_tasks":
        endpoint = f"{base}/api/v3/contents/generations/tasks"
    else:
        endpoint = f"{base}/api/v3/responses"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = datetime.utcnow()
    try:
        r = requests.post(endpoint, headers=headers, json=req_body, timeout=max(5, int(engine.timeout_s or 60)))
        ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
        text = r.text or ""
        try:
            rj = r.json()
        except Exception:
            rj = {"raw_text": text[:5000]}

        out_text = extract_output_text(rj) if isinstance(rj, dict) else ""
        contains = (engine.response_success_contains or "").strip()
        contains_ok = True if not contains else (contains in text)
        ok = bool(r.ok and contains_ok)
        hint_ok = ("测试成功" in (out_text or text)) if "测试成功" in prompt_text else None

        return jsonify(
            {
                "ok": ok,
                "status_code": int(r.status_code),
                "duration_ms": ms,
                "endpoint": endpoint,
                "output_text": (out_text or "")[:500],
                "hint_test_success": hint_ok,
                "error": None if ok else (rj.get("error") if isinstance(rj, dict) else None),
            }
        )
    except Exception as e:
        ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
        return jsonify({"ok": False, "duration_ms": ms, "error": str(e), "endpoint": endpoint}), 500


@bp.post("/api/secret/reveal")
@admin_required
def api_secret_reveal():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    confirm = (payload.get("confirm") or "").strip()
    if confirm != "SHOW":
        return jsonify({"ok": False, "error": "需要二次确认。"}), 400

    mapping = {
        "volcengine_api_key": ("volcengine_api_key", "VOLCENGINE_API_KEY"),
        "smtp_password": ("smtp_password", "FRIDGE_SMTP_PASSWORD"),
        "barcode_app_secret": ("barcode_app_secret", "FRIDGE_BARCODE_APP_SECRET"),
    }
    if name not in mapping:
        return jsonify({"ok": False, "error": "不支持的密钥项。"}), 400
    setting_key, env_key = mapping[name]
    v = get_secret_setting(setting_key=setting_key, env_key=env_key)
    return jsonify({"ok": True, "value": v or ""})

