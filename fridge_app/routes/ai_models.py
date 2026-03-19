from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for

import requests

from ..extensions import db
from ..models import AiModel, AiPromptTemplate
from ..services.settings_service import get_secret_setting
from ..utils.ark_config import build_ark_endpoint
from ..utils.ai_parse import extract_json, extract_output_text
from ..utils.auth import admin_required


bp = Blueprint("ai_models", __name__, url_prefix="/admin")


def _parse_json_maybe(raw: str) -> Any:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return json.loads(s)


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
    """
    Remove image blocks with empty urls so vision-capable templates can degrade to text-only tests.
    Supports both:
      - chat_completions: {"type":"image_url","image_url":{"url":""}}
      - responses: {"type":"input_image","image_url":""}
    """
    if isinstance(obj, dict):
        # recurse first
        out = {k: _strip_empty_image_blocks(v) for k, v in obj.items()}
        return out
    if isinstance(obj, list):
        cleaned = []
        for item in obj:
            x = _strip_empty_image_blocks(item)
            if isinstance(x, dict):
                t = (x.get("type") or "").strip()
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


def _image_to_data_url(f) -> str:
    if not f:
        return ""
    b = f.read()
    if not b:
        return ""
    mime = (getattr(f, "content_type", "") or "").strip().lower()
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        mime = "image/jpeg"
    ext = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp"}[mime]
    b64 = base64.b64encode(b).decode("utf-8")
    return f"data:image/{ext};base64,{b64}"


def _infer_ability_and_tags(m: AiModel) -> tuple[str, list[str]]:
    tpl = (m.request_template or "")
    tags: list[str] = []

    if m.api_type == "images_generations":
        ability = "图像生成"
    elif m.api_type == "contents_generations_tasks":
        ability = "图生视频"
    elif "{{image_data_url}}" in tpl:
        ability = "图片识别"
    elif "{{user_text}}" in tpl:
        ability = "文本解析"
    else:
        ability = "通用对话"

    tpl_low = tpl.lower()
    if '"stream": true' in tpl_low:
        tags.append("流式")
    if "web_search" in tpl_low:
        tags.append("联网")
    # “视觉”与“图片识别”语义重复，只有在非“图片识别”用途时才提示。
    if ability != "图片识别" and "{{image_data_url}}" in tpl:
        tags.append("视觉")
    return ability, tags


def _display_title(m: AiModel, ability: str) -> str:
    base = (m.display_name or m.name or "").strip()
    if not base:
        base = "未命名"
    if "（" in base and "）" in base:
        return base
    return f"{base}（{ability}）"


@bp.get("/ai-models")
@admin_required
def ai_models_list():
    q = (request.args.get("q") or "").strip()
    api_type = (request.args.get("api_type") or "").strip()

    query = AiModel.query
    if q:
        like = f"%{q}%"
        query = query.filter((AiModel.name.ilike(like)) | (AiModel.model_name.ilike(like)))
    if api_type:
        query = query.filter(AiModel.api_type == api_type)

    models = query.order_by(AiModel.updated_at.desc()).limit(200).all()
    view_models: list[dict[str, Any]] = []
    for m in models:
        ability, tags = _infer_ability_and_tags(m)
        view_models.append({"row": m, "title": _display_title(m, ability), "ability": ability, "tags": tags[:3]})
    prompt_templates = AiPromptTemplate.query.order_by(AiPromptTemplate.category_code, AiPromptTemplate.is_default.desc()).all()

    # Basic stats for UX
    model_counts = {}
    for m in models:
        model_counts[m.api_type] = model_counts.get(m.api_type, 0) + 1

    return render_template(
        "ai_models.html",
        models=models,
        view_models=view_models,
        q=q,
        api_type=api_type,
        model_counts=model_counts,
        prompt_templates=prompt_templates,
    )


@bp.get("/ai-models/new")
@admin_required
def ai_models_new():
    return render_template(
        "ai_model_edit.html",
        model=None,
        api_type_options=["responses", "chat_completions", "images_generations", "contents_generations_tasks"],
    )


@bp.route("/ai-models/<int:model_id>/edit", methods=["GET", "POST"])
@admin_required
def ai_models_edit(model_id: int):
    row = AiModel.query.get_or_404(model_id)
    if request.method == "GET":
        return render_template(
            "ai_model_edit.html",
            model=row,
            api_type_options=["responses", "chat_completions", "images_generations", "contents_generations_tasks"],
        )

    # POST save
    name = (request.form.get("name") or "").strip()
    display_name = (request.form.get("display_name") or "").strip()
    api_type = (request.form.get("api_type") or "responses").strip()
    base_url = (request.form.get("base_url") or "").strip()
    model_name = (request.form.get("model_name") or "").strip()
    enabled = (request.form.get("enabled") or "") == "on"

    timeout_s_raw = (request.form.get("timeout_s") or "60").strip()
    try:
        timeout_s = int(float(timeout_s_raw))
    except Exception:
        timeout_s = 60
    if timeout_s <= 0:
        timeout_s = 60

    request_template_raw = request.form.get("request_template") or ""
    request_template = request_template_raw.strip()
    if not request_template:
        flash("请求模板不能为空。", "danger")
        return redirect(url_for("ai_models.ai_models_edit", model_id=model_id))

    try:
        json.loads(request_template)
    except Exception as e:
        flash(f"请求模板 JSON 解析失败：{e}", "danger")
        return redirect(url_for("ai_models.ai_models_edit", model_id=model_id))

    headers_extra_json_raw = (request.form.get("headers_extra_json") or "{}").strip()
    try:
        headers_extra = json.loads(headers_extra_json_raw) if headers_extra_json_raw else {}
        if not isinstance(headers_extra, dict):
            raise ValueError("headers_extra_json 必须是 JSON 对象")
    except Exception as e:
        flash(f"额外请求头 JSON 解析失败：{e}", "danger")
        return redirect(url_for("ai_models.ai_models_edit", model_id=model_id))

    response_parse_mode = (request.form.get("response_parse_mode") or "auto_json").strip()
    response_success_contains = (request.form.get("response_success_contains") or "").strip()

    if not name:
        flash("模型名称不能为空。", "danger")
        return redirect(url_for("ai_models.ai_models_edit", model_id=model_id))
    if not base_url:
        flash("Base URL 不能为空。", "danger")
        return redirect(url_for("ai_models.ai_models_edit", model_id=model_id))
    if not model_name:
        flash("模型标识不能为空。", "danger")
        return redirect(url_for("ai_models.ai_models_edit", model_id=model_id))

    # Unique constraint check (best-effort)
    existed = AiModel.query.filter(AiModel.name == name, AiModel.id != row.id).first()
    if existed:
        flash("该模型名称已存在，请换一个。", "danger")
        return redirect(url_for("ai_models.ai_models_edit", model_id=model_id))

    row.name = name
    row.display_name = display_name
    row.api_type = api_type
    row.base_url = base_url
    row.model_name = model_name
    row.enabled = enabled
    row.timeout_s = timeout_s
    row.request_template = request_template
    row.headers_extra_json = json.dumps(headers_extra, ensure_ascii=False)
    row.response_parse_mode = response_parse_mode
    row.response_success_contains = response_success_contains

    db.session.commit()
    flash("模型配置已保存。", "success")
    return redirect(url_for("ai_models.ai_models_list"))


@bp.route("/ai-models", methods=["POST"])
@admin_required
def ai_models_create():
    name = (request.form.get("name") or "").strip()
    display_name = (request.form.get("display_name") or "").strip()
    api_type = (request.form.get("api_type") or "responses").strip()
    base_url = (request.form.get("base_url") or "").strip()
    model_name = (request.form.get("model_name") or "").strip()
    enabled = (request.form.get("enabled") or "") == "on"

    timeout_s_raw = (request.form.get("timeout_s") or "60").strip()
    try:
        timeout_s = int(float(timeout_s_raw))
    except Exception:
        timeout_s = 60
    if timeout_s <= 0:
        timeout_s = 60

    request_template_raw = request.form.get("request_template") or ""
    request_template = request_template_raw.strip()
    if not request_template:
        flash("请求模板不能为空。", "danger")
        return redirect(url_for("ai_models.ai_models_new"))
    try:
        json.loads(request_template)
    except Exception as e:
        flash(f"请求模板 JSON 解析失败：{e}", "danger")
        return redirect(url_for("ai_models.ai_models_new"))

    headers_extra_json_raw = (request.form.get("headers_extra_json") or "{}").strip()
    try:
        headers_extra = json.loads(headers_extra_json_raw) if headers_extra_json_raw else {}
        if not isinstance(headers_extra, dict):
            raise ValueError("headers_extra_json 必须是 JSON 对象")
    except Exception as e:
        flash(f"额外请求头 JSON 解析失败：{e}", "danger")
        return redirect(url_for("ai_models.ai_models_new"))

    response_parse_mode = (request.form.get("response_parse_mode") or "auto_json").strip()
    response_success_contains = (request.form.get("response_success_contains") or "").strip()

    if not name or not base_url or not model_name:
        flash("模型名称/Base URL/模型标识均不能为空。", "danger")
        return redirect(url_for("ai_models.ai_models_new"))

    existed = AiModel.query.filter_by(name=name).first()
    if existed:
        flash("该模型名称已存在，请直接编辑。", "danger")
        return redirect(url_for("ai_models.ai_models_list"))

    db.session.add(
        AiModel(
            name=name,
            display_name=display_name,
            api_type=api_type,
            base_url=base_url,
            model_name=model_name,
            enabled=enabled,
            request_template=request_template,
            response_parse_mode=response_parse_mode,
            response_success_contains=response_success_contains,
            headers_extra_json=json.dumps(headers_extra, ensure_ascii=False),
            timeout_s=timeout_s,
        )
    )
    db.session.commit()
    flash("模型已创建。", "success")
    return redirect(url_for("ai_models.ai_models_list"))


@bp.post("/ai-models/<int:model_id>/delete")
@admin_required
def ai_models_delete(model_id: int):
    row = AiModel.query.get_or_404(model_id)
    # UI should confirm; back-end double-check enabled/exists.
    db.session.delete(row)
    db.session.commit()
    flash("模型已删除。", "success")
    return redirect(url_for("ai_models.ai_models_list"))


@bp.route("/ai-models/<int:model_id>/test", methods=["GET", "POST"])
@admin_required
def ai_models_test(model_id: int):
    row = AiModel.query.get_or_404(model_id)
    prompt_templates = AiPromptTemplate.query.order_by(AiPromptTemplate.category_code, AiPromptTemplate.is_default.desc()).all()
    # MVP: default to interface_test category connectivity prompt.
    default_prompt_template = (
        AiPromptTemplate.query.filter_by(category_code="interface_test", is_default=True)
        .order_by(AiPromptTemplate.updated_at.desc())
        .first()
    )
    if not default_prompt_template:
        default_prompt_template = next((p for p in prompt_templates if p.is_default), None)
    selected_prompt_template_id = default_prompt_template.id if default_prompt_template else None

    result: dict[str, Any] = {}
    if request.method == "POST":
        prompt_template_id = (request.form.get("prompt_template_id") or "").strip()
        user_text = (request.form.get("user_text") or "").strip()

        prompt_row = None
        if prompt_template_id:
            try:
                pid = int(prompt_template_id)
                prompt_row = AiPromptTemplate.query.get(pid)
            except Exception:
                prompt_row = None
        prompt_content = (prompt_row.content if prompt_row else "") or ""

        uploaded_image_data_url = _image_to_data_url(request.files.get("image"))

        # Auth
        api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
        if not api_key:
            flash("未配置 VOLCENGINE_API_KEY：请到 设置->安全设置 设置。", "danger")
            return redirect(url_for("ai_models.ai_models_test", model_id=model_id))

        request_template_raw = (row.request_template or "").strip()
        try:
            payload_template = json.loads(request_template_raw) if request_template_raw else {}
        except Exception as e:
            flash(f"请求模板 JSON 解析失败：{e}", "danger")
            return redirect(url_for("ai_models.ai_models_test", model_id=model_id))

        mapping = {
            "{{model}}": str(row.model_name or ""),
            "{{prompt}}": prompt_content,
            "{{user_text}}": user_text,
            "{{image_data_url}}": uploaded_image_data_url,
        }
        payload = _deep_replace(payload_template, mapping)
        payload = _strip_empty_image_blocks(payload)
        request_payload_pretty = None
        try:
            request_payload_pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception:
            request_payload_pretty = None

        headers_extra = {}
        try:
            headers_extra = json.loads(row.headers_extra_json or "{}") if row.headers_extra_json else {}
            if not isinstance(headers_extra, dict):
                headers_extra = {}
        except Exception:
            headers_extra = {}

        endpoint = build_ark_endpoint({"base_url": row.base_url, "api_type": row.api_type})

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        for k, v in headers_extra.items():
            if k in {"Authorization", "Content-Type"}:
                continue
            headers[str(k)] = str(v)

        t0 = time.time()
        try:
            wants_stream = bool(payload.get("stream") is True)
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=row.timeout_s, stream=wants_stream)
            duration_ms = int((time.time() - t0) * 1000)
            status_code = int(resp.status_code)
            resp_text = resp.text or ""
            stream_text = ""
            stream_events: list[dict[str, Any]] = []
            if wants_stream:
                # Best-effort SSE parsing: data: <json>
                # Collect delta text if present.
                chunks: list[str] = []
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
                        if isinstance(ev, dict):
                            stream_events.append(ev)
                            # Compatible with your sample: response.output_text.delta
                            if ev.get("type") == "response.output_text.delta":
                                delta = ev.get("delta", "")
                                if isinstance(delta, str) and delta:
                                    chunks.append(delta)
                    except Exception:
                        continue
                stream_text = "".join(chunks)
                # For stream mode, avoid reading resp.text again (already consumed).
                resp_text = stream_text or ""
            try:
                resp_json = resp.json()
            except Exception:
                resp_json = {"raw_text": resp_text, "stream_events": stream_events} if wants_stream else {"raw_text": resp_text}

            response_json_pretty = None
            try:
                if isinstance(resp_json, (dict, list)):
                    response_json_pretty = json.dumps(resp_json, ensure_ascii=False, indent=2)
            except Exception:
                response_json_pretty = None

            # Backend logging for connectivity tests
            verbose_raw = (os.environ.get("AI_LOG_VERBOSE") or "1").strip().lower()
            verbose = verbose_raw in {"1", "true", "yes", "on"}
            full_raw = (os.environ.get("AI_LOG_FULL_RESPONSE") or "1").strip().lower()
            full_response = full_raw in {"1", "true", "yes", "on"}
            if verbose:
                print("=" * 80)
                print("[AI][MODEL_TEST] start")
                print("model_id:", row.id)
                print("model_name:", row.name)
                print("endpoint:", endpoint)
                print("status_code:", status_code)
                print("duration_ms:", duration_ms)
                if request_payload_pretty:
                    print("request_payload:\n", request_payload_pretty)
                if full_response and response_json_pretty:
                    print("response_json:\n", response_json_pretty)
                elif full_response:
                    print("response_text:\n", resp_text[:5000])
                print("=" * 80)

            extracted = None
            parse_mode = (row.response_parse_mode or "auto_json").strip()
            out_text_nonempty = False
            if parse_mode == "auto_json" and isinstance(resp_json, dict):
                out_text = extract_output_text(resp_json)
                out_text_nonempty = bool(str(out_text or "").strip())
                extracted = extract_json(out_text) if out_text else {}

            result = {
                "ok": False,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "endpoint": endpoint,
                "request_payload": payload,
                "request_payload_pretty": request_payload_pretty,
                "response_json": resp_json,
                "response_json_pretty": response_json_pretty,
                "parsed": extracted,
                "stream_text": stream_text,
            }

            http_ok = 200 <= status_code < 300
            if row.response_success_contains:
                result["contains_match"] = row.response_success_contains in resp_text
            else:
                result["contains_match"] = True

            contains_ok = bool(result["contains_match"])

            # Connectivity rule (MVP):
            # - HTTP 2xx + (optional contains_match) => 通畅
            # - Don't require JSON parse success for connectivity.
            if http_ok and contains_ok:
                if parse_mode == "raw":
                    result["ok"] = True
                elif parse_mode == "auto_json":
                    # If server returned something usable, treat as connectivity success.
                    result["ok"] = out_text_nonempty or extracted is not None
                else:
                    result["ok"] = True
            else:
                result["ok"] = False

            # parsed pretty
            try:
                if extracted is not None:
                    result["parsed_pretty"] = json.dumps(extracted, ensure_ascii=False, indent=2)
                else:
                    result["parsed_pretty"] = None
            except Exception:
                result["parsed_pretty"] = None
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            result = {
                "ok": False,
                "duration_ms": duration_ms,
                "error": str(e),
                "endpoint": endpoint,
                "request_payload": payload,
                "request_payload_pretty": request_payload_pretty,
            }

    return render_template(
        "ai_model_test.html",
        model=row,
        prompt_templates=prompt_templates,
        result=result,
        selected_prompt_template_id=selected_prompt_template_id,
    )

