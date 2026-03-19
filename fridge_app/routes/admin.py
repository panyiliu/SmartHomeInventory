from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

import requests

from ..extensions import db
from ..models import EmailLog, Item
from ..services.email_service import send_digest_email
from ..services.settings_service import (
    dump_option_list,
    dump_json_object,
    get_int_setting,
    get_secret_setting,
    get_setting,
    parse_json_object,
    parse_option_list,
    set_setting,
    DEFAULT_CATEGORY_ICON_MAP,
    DEFAULT_LOCATION_ICON_MAP,
    DEFAULT_CATEGORY_SHORT_LABEL_MAP,
    DEFAULT_LOCATION_SHORT_LABEL_MAP,
)
from ..models import AiModel
from ..models import AiPromptTemplate
from ..utils.ai_parse import extract_output_text


bp = Blueprint("admin", __name__, url_prefix="/admin")


def _mask_tail(v: str, *, keep: int = 3) -> str:
    s = (v or "").strip()
    if not s:
        return ""
    k = max(2, min(int(keep), 6))
    tail = s[-k:] if len(s) >= k else s
    return "*" * 8 + tail


@bp.get("/settings")
def admin_settings():
    default_categories = ["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "其他"]
    default_locations = ["冰箱", "冷藏", "冷冻", "常温", "橱柜", "厨房", "室外", "卫生间"]
    cat_list = parse_option_list(get_setting("category_options", ""), default=default_categories)
    loc_list = parse_option_list(get_setting("location_options", ""), default=default_locations)
    cat_icon_obj = parse_json_object(get_setting("category_icon_map_json", ""), default=DEFAULT_CATEGORY_ICON_MAP)
    if not cat_icon_obj:
        cat_icon_obj = dict(DEFAULT_CATEGORY_ICON_MAP)
    loc_icon_obj = parse_json_object(get_setting("location_icon_map_json", ""), default=DEFAULT_LOCATION_ICON_MAP)
    if not loc_icon_obj:
        loc_icon_obj = dict(DEFAULT_LOCATION_ICON_MAP)
    cat_label_obj = parse_json_object(get_setting("category_label_map_json", ""), default=DEFAULT_CATEGORY_SHORT_LABEL_MAP)
    if not cat_label_obj:
        cat_label_obj = dict(DEFAULT_CATEGORY_SHORT_LABEL_MAP)
    loc_label_obj = parse_json_object(get_setting("location_label_map_json", ""), default=DEFAULT_LOCATION_SHORT_LABEL_MAP)
    if not loc_label_obj:
        loc_label_obj = dict(DEFAULT_LOCATION_SHORT_LABEL_MAP)

    category_icon_map_json = dump_json_object(cat_icon_obj)
    location_icon_map_json = dump_json_object(loc_icon_obj)
    category_label_map_json = dump_json_object(cat_label_obj)
    location_label_map_json = dump_json_object(loc_label_obj)

    # feature toggles
    notify_enabled = (get_setting("notify_enabled", "1") or "1").strip() != "0"
    ai_enabled = (get_setting("ai_enabled", "1") or "1").strip() != "0"
    digest_enabled = (get_setting("digest_enabled", "1") or "1").strip() != "0"

    # secret tails (env override)
    ark_key_val = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    smtp_pwd_val = get_secret_setting(setting_key="smtp_password", env_key="FRIDGE_SMTP_PASSWORD")
    barcode_secret_val = get_secret_setting(setting_key="barcode_app_secret", env_key="FRIDGE_BARCODE_APP_SECRET")

    email_history_count = EmailLog.query.count()

    # New: ability -> engine (AiModel) selection
    engines = AiModel.query.order_by(AiModel.enabled.desc(), AiModel.updated_at.desc()).all()
    enabled_engines = [e for e in engines if e.enabled]

    def _caps(e: AiModel) -> set[str]:
        raw = (e.capabilities_json or "[]").strip()
        try:
            arr = json.loads(raw) if raw else []
            if isinstance(arr, list):
                return {str(x) for x in arr if str(x)}
        except Exception:
            pass
        return set()

    vision_engines = [e for e in enabled_engines if "vision_recognize" in _caps(e)]
    text_engines = [e for e in enabled_engines if "text_extract" in _caps(e)]
    recipes_engines = [e for e in enabled_engines if "recipes_generate" in _caps(e)]
    def _sel(key: str) -> str:
        return (get_setting(key, "") or "").strip()

    def _get_engine(engine_id: str) -> AiModel | None:
        try:
            if not engine_id:
                return None
            return AiModel.query.get(int(engine_id))
        except Exception:
            return None

    engine_vision = _get_engine(_sel("ai_engine_vision_model_id"))
    engine_text = _get_engine(_sel("ai_engine_text_model_id"))
    engine_recipes = _get_engine(_sel("ai_engine_recipes_model_id"))
    # (MVP) only keep abilities already used in app: vision/text/recipes

    return render_template(
        "admin_settings.html",
        expiring_soon_days=get_int_setting("expiring_soon_days", 3),
        ark_key_is_set=bool(ark_key_val),
        ark_key_tail=_mask_tail(ark_key_val, keep=4) if ark_key_val else "",
        smtp_host=get_setting("smtp_host", "smtp.qq.com"),
        smtp_port=get_int_setting("smtp_port", 465),
        smtp_ssl=get_setting("smtp_ssl", "1"),
        smtp_user=get_setting("smtp_user", ""),
        smtp_to=get_setting("smtp_to", ""),
        password_is_set=bool(smtp_pwd_val),
        smtp_password_tail=_mask_tail(smtp_pwd_val, keep=3) if smtp_pwd_val else "",
        barcode_app_id=get_setting("barcode_app_id", ""),
        barcode_secret_is_set=bool(barcode_secret_val),
        barcode_secret_tail=_mask_tail(barcode_secret_val, keep=4) if barcode_secret_val else "",
        category_options_json=dump_option_list(cat_list),
        location_options_json=dump_option_list(loc_list),
        category_icon_map_json=category_icon_map_json,
        location_icon_map_json=location_icon_map_json,
        category_label_map_json=category_label_map_json,
        location_label_map_json=location_label_map_json,
        notify_enabled=notify_enabled,
        ai_enabled=ai_enabled,
        digest_enabled=digest_enabled,
        email_history_count=email_history_count,
        ai_engines=enabled_engines,
        vision_engines=vision_engines,
        text_engines=text_engines,
        recipes_engines=recipes_engines,
        ai_engine_vision_id=_sel("ai_engine_vision_model_id"),
        ai_engine_text_id=_sel("ai_engine_text_model_id"),
        ai_engine_recipes_id=_sel("ai_engine_recipes_model_id"),
        engine_vision=engine_vision,
        engine_text=engine_text,
        engine_recipes=engine_recipes,
    )


@bp.post("/settings/general")
def settings_save_general():
    set_setting("expiring_soon_days", (request.form.get("expiring_soon_days") or "3").strip())
    flash("基础设置已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-general"))


@bp.post("/settings/data")
def settings_save_data():
    # options stored as JSON arrays
    cat_raw = (request.form.get("category_options") or "").strip()
    loc_raw = (request.form.get("location_options") or "").strip()
    try:
        json.loads(cat_raw) if cat_raw else []
        json.loads(loc_raw) if loc_raw else []
    except Exception:
        flash("保存失败：分类/位置选项格式不正确。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-data"))
    if cat_raw:
        set_setting("category_options", cat_raw)
    if loc_raw:
        set_setting("location_options", loc_raw)

    # icon/label maps: stored as JSON objects
    cat_icon_raw = (request.form.get("category_icon_map_json") or "").strip()
    loc_icon_raw = (request.form.get("location_icon_map_json") or "").strip()
    cat_label_raw = (request.form.get("category_label_map_json") or "").strip()
    loc_label_raw = (request.form.get("location_label_map_json") or "").strip()
    try:
        json.loads(cat_icon_raw) if cat_icon_raw else {}
        json.loads(loc_icon_raw) if loc_icon_raw else {}
        json.loads(cat_label_raw) if cat_label_raw else {}
        json.loads(loc_label_raw) if loc_label_raw else {}
    except Exception:
        flash("保存失败：图标/短标签映射 JSON 格式不正确。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-data"))
    if cat_icon_raw:
        set_setting("category_icon_map_json", cat_icon_raw)
    if loc_icon_raw:
        set_setting("location_icon_map_json", loc_icon_raw)
    if cat_label_raw:
        set_setting("category_label_map_json", cat_label_raw)
    if loc_label_raw:
        set_setting("location_label_map_json", loc_label_raw)
    flash("数据与选项已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-data"))


@bp.post("/settings/notify")
def settings_save_notify():
    set_setting("notify_enabled", "1" if (request.form.get("notify_enabled") or "") == "1" else "0")
    set_setting("ai_enabled", "1" if (request.form.get("ai_enabled") or "") == "1" else "0")
    set_setting("digest_enabled", "1" if (request.form.get("digest_enabled") or "") == "1" else "0")
    set_setting("smtp_host", (request.form.get("smtp_host") or "").strip())
    set_setting("smtp_port", (request.form.get("smtp_port") or "465").strip())
    set_setting("smtp_ssl", "1" if (request.form.get("smtp_ssl") or "") == "1" else "0")
    set_setting("smtp_user", (request.form.get("smtp_user") or "").strip())
    set_setting("smtp_to", (request.form.get("smtp_to") or "").strip())
    flash("通知与 AI 已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-notify-ai"))


@bp.post("/settings/integrations")
def settings_save_integrations():
    set_setting("barcode_app_id", (request.form.get("barcode_app_id") or "").strip())
    flash("集成配置已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-integrations"))

@bp.post("/settings/security")
def settings_save_security():
    new_ark_key = (request.form.get("volcengine_api_key") or "").strip()
    if new_ark_key:
        set_setting("volcengine_api_key", new_ark_key)

    new_pwd = (request.form.get("smtp_password") or "").strip()
    if new_pwd:
        set_setting("smtp_password", new_pwd)

    new_barcode_secret = (request.form.get("barcode_app_secret") or "").strip()
    if new_barcode_secret:
        set_setting("barcode_app_secret", new_barcode_secret)

    flash("安全中心已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-security"))


@bp.post("/settings/ai")
def settings_save_ai():
    vision_active = (request.form.get("ark_active_profile_vision") or request.form.get("ark_active_profile") or "default").strip()
    text_active = (request.form.get("ark_active_profile_text") or request.form.get("ark_active_profile") or "default").strip()
    recipes_active = (request.form.get("ark_active_profile_recipes") or request.form.get("ark_active_profile") or "default").strip()
    image_gen_active = (request.form.get("ark_active_profile_image_generation") or "default").strip()
    video_task_active = (request.form.get("ark_active_profile_video_task") or "default").strip()
    raw_profiles = (request.form.get("ark_profiles_json") or "").strip()

    if not raw_profiles:
        # Reset to defaults (keep active selection as-is)
        set_setting("ark_profiles", "")
        set_setting("ark_active_profile", vision_active)
        set_setting("ark_active_profile_vision", vision_active)
        set_setting("ark_active_profile_text", text_active)
        set_setting("ark_active_profile_recipes", recipes_active)
        set_setting("ark_active_profile_image_generation", image_gen_active)
        set_setting("ark_active_profile_video_task", video_task_active)
        flash("AI 配置已重置为默认值（激活项已按你选择保存）。", "success")
        return redirect(url_for("admin.admin_settings", _anchor="sec-ai"))

    try:
        parsed = json.loads(raw_profiles)
    except Exception:
        flash("保存失败：AI 配置 JSON 解析失败。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-ai"))

    if not isinstance(parsed, list):
        flash("保存失败：AI 配置 JSON 必须是一个数组（list）。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-ai"))

    normalized: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        normalized.append(item)

    set_setting("ark_profiles", json.dumps(normalized, ensure_ascii=False))
    # legacy global active
    set_setting("ark_active_profile", vision_active)
    # capability-specific active
    set_setting("ark_active_profile_vision", vision_active)
    set_setting("ark_active_profile_text", text_active)
    set_setting("ark_active_profile_recipes", recipes_active)
    set_setting("ark_active_profile_image_generation", image_gen_active)
    set_setting("ark_active_profile_video_task", video_task_active)
    flash("AI 配置已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-ai"))


@bp.post("/settings/ai-engines")
def settings_save_ai_engines():
    set_setting("ai_engine_vision_model_id", (request.form.get("ai_engine_vision_model_id") or "").strip())
    set_setting("ai_engine_text_model_id", (request.form.get("ai_engine_text_model_id") or "").strip())
    set_setting("ai_engine_recipes_model_id", (request.form.get("ai_engine_recipes_model_id") or "").strip())
    flash("能力引擎选择已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-ai"))


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
def api_ai_engine_test():
    """
    Quick connectivity test for selected AiModel engine.
    Uses default interface_test prompt template. No image is provided.
    """
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
        # fallback to settings secret storage
        from ..services.settings_service import get_secret_setting

        api_key = get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY")
    if not api_key:
        return jsonify({"ok": False, "error": "未配置 VOLCENGINE_API_KEY"}), 400

    prompt_row = (
        AiPromptTemplate.query.filter_by(category_code="interface_test", is_default=True)
        .order_by(AiPromptTemplate.updated_at.desc())
        .first()
    )
    prompt_text = (prompt_row.content if prompt_row else "") or "你好，请回复“测试成功”。"

    # Build request payload from engine template
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

    # Force non-stream for quick test
    if isinstance(req_body, dict) and req_body.get("stream") is True:
        req_body["stream"] = False

    endpoint = ""
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
        # If interface_test prompt expects 测试成功, use it as a friendly hint
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
def api_secret_reveal():
    """
    Temporary reveal of secrets for troubleshooting.
    NOTE: This project currently has no user auth system, so we require an explicit confirmation token.
    """
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
    if not v:
        return jsonify({"ok": True, "value": ""})
    return jsonify({"ok": True, "value": v})


# Backward-compatible endpoint (kept, but UI no longer posts here)
@bp.post("/settings")
def admin_settings_save():
    flash("设置页已升级为“分模块保存”。请在对应模块内点击保存按钮。", "warning")
    return redirect(url_for("admin.admin_settings"))


@bp.post("/send-digest")
def admin_send_digest():
    expiring_soon_days = get_int_setting("expiring_soon_days", 3)
    now = datetime.utcnow()

    active = Item.query.all()
    expired: list[tuple[Item, int]] = []
    expiring: list[tuple[Item, int]] = []
    for it in active:
        remain = it.remaining_days(now=now)
        if remain is None:
            continue
        if remain < 0:
            expired.append((it, remain))
        elif remain <= expiring_soon_days:
            expiring.append((it, remain))

    subject = f"冰箱食材提醒：过期 {len(expired)}，临期 {len(expiring)}（阈值 {expiring_soon_days} 天）"

    def line(it: Item, remain: int) -> str:
        stored = it.stored_days(now=now)
        if remain < 0:
            return f"- 【过期 {abs(remain)} 天】{it.name}  数量 {it.quantity:g}{it.unit}  已存放 {stored} 天  位置 {it.location}"
        return f"- 【剩余 {remain} 天】{it.name}  数量 {it.quantity:g}{it.unit}  已存放 {stored} 天  位置 {it.location}"

    body_lines = [
        "冰箱食材汇总（自动生成）：",
        "",
        f"临期（<= {expiring_soon_days} 天）：",
        *(line(it, remain) for it, remain in sorted(expiring, key=lambda x: x[1])),
        "",
        "已过期：",
        *(line(it, remain) for it, remain in sorted(expired, key=lambda x: x[1])),
        "",
        f"生成时间（UTC）：{now.strftime('%Y-%m-%d %H:%M')}",
    ]
    body = "\n".join(body_lines)

    ok, err = send_digest_email(subject=subject, body=body)
    log = EmailLog(
        to_emails=get_setting("smtp_to", ""),
        subject=subject,
        status="sent" if ok else "failed",
        error=err,
    )
    db.session.add(log)
    db.session.commit()

    if ok:
        flash("已发送提醒邮件。", "success")
    else:
        flash(f"发送失败：{err}", "danger")
    return redirect(url_for("admin.admin_settings"))


@bp.get("/email-history")
def admin_email_history():
    logs = EmailLog.query.order_by(EmailLog.id.desc()).limit(50).all()
    return render_template("admin_email_history.html", logs=logs)

