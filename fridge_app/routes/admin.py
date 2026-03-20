from __future__ import annotations

import json
import os
import io
import csv
from datetime import datetime
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app, jsonify

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
    normalize_icon_spec,
)
from ..models import AiModel
from ..models import AiPromptTemplate
from ..utils.auth import admin_required
from ..utils.ai_text import generate_icon_candidates_for_names
from ..services.ai_job_service import ai_job_service


bp = Blueprint("admin", __name__, url_prefix="/admin")


def _mask_tail(v: str, *, keep: int = 3) -> str:
    s = (v or "").strip()
    if not s:
        return ""
    k = max(2, min(int(keep), 6))
    tail = s[-k:] if len(s) >= k else s
    return "*" * 8 + tail


@bp.get("/settings")
@admin_required
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
    b2_access_key_id_val = get_secret_setting(setting_key="backup_b2_access_key_id", env_key="B2_ACCESS_KEY_ID")
    b2_application_key_val = get_secret_setting(setting_key="backup_b2_application_key", env_key="B2_APPLICATION_KEY")

    backup_enabled_val = (os.environ.get("BACKUP_ENABLED") or get_setting("backup_enabled", "0") or "0").strip()
    backup_cron_val = (os.environ.get("BACKUP_FREQUENCY_CRON") or get_setting("backup_frequency_cron", "") or "").strip()
    if not backup_cron_val:
        backup_cron_val = "0 3 * * *"

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
    engine_icon_suggest = _get_engine(_sel("ai_engine_icon_suggest_model_id"))
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
        backup_b2_endpoint=(os.environ.get("B2_ENDPOINT") or get_setting("backup_b2_endpoint", "")).strip(),
        backup_b2_bucket_name=(os.environ.get("B2_BUCKET_NAME") or get_setting("backup_b2_bucket_name", "")).strip(),
        backup_b2_region=(os.environ.get("B2_REGION") or get_setting("backup_b2_region", "us-east-1")).strip() or "us-east-1",
        backup_source_path=(os.environ.get("BACKUP_SOURCE_PATH") or get_setting("backup_source_path", "/app/instance/items-backup.csv")).strip() or "/app/instance/items-backup.csv",
        backup_target_key=(os.environ.get("BACKUP_TARGET_KEY") or get_setting("backup_target_key", "latest_backup.csv")).strip() or "latest_backup.csv",
        b2_access_key_id_is_set=bool(b2_access_key_id_val),
        b2_access_key_id_tail=_mask_tail(b2_access_key_id_val, keep=4) if b2_access_key_id_val else "",
        b2_application_key_is_set=bool(b2_application_key_val),
        b2_application_key_tail=_mask_tail(b2_application_key_val, keep=4) if b2_application_key_val else "",
        backup_enabled=(backup_enabled_val or "0").strip() in {"1", "true", "True", "yes", "on", "ON"},
        backup_frequency_cron=backup_cron_val,
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
        engine_icon_suggest=engine_icon_suggest,
        ai_engine_icon_suggest_id=_sel("ai_engine_icon_suggest_model_id"),
    )


@bp.post("/settings/general")
@admin_required
def settings_save_general():
    set_setting("expiring_soon_days", (request.form.get("expiring_soon_days") or "3").strip())
    flash("基础设置已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-general"))


@bp.post("/settings/data")
@admin_required
def settings_save_data():
    # options stored as JSON arrays
    cat_raw = (request.form.get("category_options") or "").strip()
    loc_raw = (request.form.get("location_options") or "").strip()
    try:
        json.loads(cat_raw) if cat_raw else []
        json.loads(loc_raw) if loc_raw else []
    except Exception:
        flash("保存失败：分类/位置选项格式不正确。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-data-manage"))
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
        return redirect(url_for("admin.admin_settings", _anchor="sec-data-manage"))
    if cat_icon_raw:
        set_setting("category_icon_map_json", cat_icon_raw)
    if loc_icon_raw:
        set_setting("location_icon_map_json", loc_icon_raw)
    if cat_label_raw:
        set_setting("category_label_map_json", cat_label_raw)
    if loc_label_raw:
        set_setting("location_label_map_json", loc_label_raw)
    flash("数据与选项已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-data-manage"))


def _calc_missing_icon_keys(*, options: list[str], merged_icon_map: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for key in options or []:
        k = str(key or "").strip()
        if not k:
            continue
        norm = normalize_icon_spec(merged_icon_map.get(k))
        if norm.get("type") == "none":
            missing.append(k)
    return missing


@bp.post("/icons/missing/preview")
@admin_required
def icons_missing_preview():
    payload = request.get_json(silent=True) or {}
    limit = int(payload.get("limit") or 50)
    limit = max(1, min(limit, 200))

    default_categories = ["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "其他"]
    default_locations = ["冰箱", "冷藏", "冷冻", "常温", "橱柜", "厨房", "室外", "卫生间"]
    cat_list = parse_option_list(get_setting("category_options", ""), default=default_categories)
    loc_list = parse_option_list(get_setting("location_options", ""), default=default_locations)

    cat_icon_custom = parse_json_object(get_setting("category_icon_map_json", ""), default={})
    loc_icon_custom = parse_json_object(get_setting("location_icon_map_json", ""), default={})

    cat_merged = dict(DEFAULT_CATEGORY_ICON_MAP)
    cat_merged.update(cat_icon_custom)
    loc_merged = dict(DEFAULT_LOCATION_ICON_MAP)
    loc_merged.update(loc_icon_custom)

    missing_cats = _calc_missing_icon_keys(options=cat_list, merged_icon_map=cat_merged)
    missing_locs = _calc_missing_icon_keys(options=loc_list, merged_icon_map=loc_merged)

    total_cats = len(missing_cats)
    total_locs = len(missing_locs)
    cats = missing_cats[:limit]
    locs = missing_locs[:limit]

    return jsonify(
        {
            "ok": True,
            "counts": {"categories_missing": total_cats, "locations_missing": total_locs, "total_missing": total_cats + total_locs},
            "missing_categories": cats,
            "missing_locations": locs,
            "limit": limit,
        }
    )


@bp.post("/icons/missing/generate")
@admin_required
def icons_missing_generate():
    payload = request.get_json(silent=True) or {}
    max_items = int(payload.get("max_items") or 20)
    candidates_per_item = int(payload.get("candidates_per_item") or 3)
    max_items = max(1, min(max_items, 80))
    candidates_per_item = max(1, min(candidates_per_item, 5))

    default_categories = ["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "其他"]
    default_locations = ["冰箱", "冷藏", "冷冻", "常温", "橱柜", "厨房", "室外", "卫生间"]
    cat_list = parse_option_list(get_setting("category_options", ""), default=default_categories)
    loc_list = parse_option_list(get_setting("location_options", ""), default=default_locations)

    cat_icon_custom = parse_json_object(get_setting("category_icon_map_json", ""), default={})
    loc_icon_custom = parse_json_object(get_setting("location_icon_map_json", ""), default={})

    cat_merged = dict(DEFAULT_CATEGORY_ICON_MAP)
    cat_merged.update(cat_icon_custom)
    loc_merged = dict(DEFAULT_LOCATION_ICON_MAP)
    loc_merged.update(loc_icon_custom)

    missing_cats = _calc_missing_icon_keys(options=cat_list, merged_icon_map=cat_merged)
    missing_locs = _calc_missing_icon_keys(options=loc_list, merged_icon_map=loc_merged)

    ordered: list[tuple[str, str]] = [("category", c) for c in missing_cats] + [("location", l) for l in missing_locs]
    ordered = ordered[:max_items]

    cats_to_gen = [k for kind, k in ordered if kind == "category"]
    locs_to_gen = [k for kind, k in ordered if kind == "location"]

    if not cats_to_gen and not locs_to_gen:
        return jsonify({"ok": True, "message": "没有需要生成的缺失图标。", "applied": {"categories": 0, "locations": 0}})

    applied_categories = 0
    applied_locations = 0
    missing_cat_suggest_keys: list[str] = []
    missing_loc_suggest_keys: list[str] = []
    cat_suggest: dict[str, list[str]] = {}
    loc_suggest: dict[str, list[str]] = {}

    if cats_to_gen:
        cat_suggest = generate_icon_candidates_for_names(cats_to_gen, kind="category", candidates_per_item=candidates_per_item) or {}
        if not cat_suggest:
            missing_cat_suggest_keys = list(cats_to_gen)
        for name in cats_to_gen:
            cands = cat_suggest.get(name) or []
            if not cands:
                if name not in missing_cat_suggest_keys:
                    missing_cat_suggest_keys.append(name)
                continue
            best = cands[0]
            if best and best != cat_merged.get(name):
                cat_merged[name] = best
                applied_categories += 1
            elif best:
                applied_categories += 1  # treat as applied even if already set to same best

    if locs_to_gen:
        loc_suggest = generate_icon_candidates_for_names(locs_to_gen, kind="location", candidates_per_item=candidates_per_item) or {}
        if not loc_suggest:
            missing_loc_suggest_keys = list(locs_to_gen)
        for name in locs_to_gen:
            cands = loc_suggest.get(name) or []
            if not cands:
                if name not in missing_loc_suggest_keys:
                    missing_loc_suggest_keys.append(name)
                continue
            best = cands[0]
            if best and best != loc_merged.get(name):
                loc_merged[name] = best
                applied_locations += 1
            elif best:
                applied_locations += 1

    if not (cat_suggest or loc_suggest):
        return jsonify({"ok": False, "error": "AI 图标生成不可用（未配置文本引擎/引擎不可调用）"}), 400

    # Persist only when we successfully apply at least one icon for that kind.
    if applied_categories > 0:
        set_setting("category_icon_map_json", dump_json_object(cat_merged))
    if applied_locations > 0:
        set_setting("location_icon_map_json", dump_json_object(loc_merged))

    return jsonify(
        {
            "ok": True,
            "applied": {"categories": applied_categories, "locations": applied_locations},
            "generated_requested": {"categories": len(cats_to_gen), "locations": len(locs_to_gen)},
            "ai_missing_keys": {"categories": missing_cat_suggest_keys, "locations": missing_loc_suggest_keys},
        }
    )


def _calc_missing_icon_counts(*, cat_list: list[str], loc_list: list[str], cat_merged: dict[str, str], loc_merged: dict[str, str]) -> dict[str, int]:
    missing_cats = _calc_missing_icon_keys(options=cat_list, merged_icon_map=cat_merged)
    missing_locs = _calc_missing_icon_keys(options=loc_list, merged_icon_map=loc_merged)
    return {
        "categories_missing": len(missing_cats),
        "locations_missing": len(missing_locs),
        "total_missing": len(missing_cats) + len(missing_locs),
    }


def _compute_missing_icon_candidates(*, max_items: int, candidates_per_item: int) -> dict[str, Any]:
    default_categories = ["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "其他"]
    default_locations = ["冰箱", "冷藏", "冷冻", "常温", "橱柜", "厨房", "室外", "卫生间"]
    cat_list = parse_option_list(get_setting("category_options", ""), default=default_categories)
    loc_list = parse_option_list(get_setting("location_options", ""), default=default_locations)

    cat_icon_custom = parse_json_object(get_setting("category_icon_map_json", ""), default={})
    loc_icon_custom = parse_json_object(get_setting("location_icon_map_json", ""), default={})

    cat_merged = dict(DEFAULT_CATEGORY_ICON_MAP)
    cat_merged.update(cat_icon_custom)
    loc_merged = dict(DEFAULT_LOCATION_ICON_MAP)
    loc_merged.update(loc_icon_custom)

    missing_counts = _calc_missing_icon_counts(
        cat_list=cat_list,
        loc_list=loc_list,
        cat_merged=cat_merged,
        loc_merged=loc_merged,
    )

    missing_cats = _calc_missing_icon_keys(options=cat_list, merged_icon_map=cat_merged)
    missing_locs = _calc_missing_icon_keys(options=loc_list, merged_icon_map=loc_merged)

    ordered: list[tuple[str, str]] = [("category", c) for c in missing_cats] + [("location", l) for l in missing_locs]
    ordered = ordered[:max_items]

    cats_to_gen = [k for kind, k in ordered if kind == "category"]
    locs_to_gen = [k for kind, k in ordered if kind == "location"]

    cat_suggest = generate_icon_candidates_for_names(cats_to_gen, kind="category", candidates_per_item=candidates_per_item) or {}
    loc_suggest = generate_icon_candidates_for_names(locs_to_gen, kind="location", candidates_per_item=candidates_per_item) or {}

    # Stable payload for UI:
    # - always include keys we tried generating for (even if candidates empty)
    cat_candidates = {name: (cat_suggest.get(name) or []) for name in cats_to_gen}
    loc_candidates = {name: (loc_suggest.get(name) or []) for name in locs_to_gen}

    return {
        "counts": missing_counts,
        "generated_requested": {"categories": len(cats_to_gen), "locations": len(locs_to_gen)},
        "category_candidates": cat_candidates,
        "location_candidates": loc_candidates,
    }


@bp.post("/icons/missing/suggest-async")
@admin_required
def icons_missing_suggest_async():
    payload = request.get_json(silent=True) or {}
    max_items = int(payload.get("max_items") or 20)
    candidates_per_item = int(payload.get("candidates_per_item") or 3)
    max_items = max(1, min(max_items, 80))
    candidates_per_item = max(1, min(candidates_per_item, 5))

    # Quick reject: no candidates -> return immediately.
    def _fn() -> dict[str, Any]:
        return _compute_missing_icon_candidates(max_items=max_items, candidates_per_item=candidates_per_item)

    job_id = ai_job_service.create_job(
        kind="icons_missing_candidates",
        fn=_fn,
        meta={"max_items": max_items, "candidates_per_item": candidates_per_item},
        app=current_app._get_current_object(),
    )
    return jsonify({"ok": True, "async": True, "job_id": job_id})


@bp.post("/icons/missing/apply")
@admin_required
def icons_missing_apply():
    payload = request.get_json(silent=True) or {}
    job_id = str(payload.get("job_id") or "").strip()
    sel_categories = payload.get("categories") or {}
    sel_locations = payload.get("locations") or {}
    if not job_id:
        return jsonify({"ok": False, "error": "job_id 不能为空"}), 400

    job = ai_job_service.get_job(job_id)
    if not job or job.status != "success":
        return jsonify({"ok": False, "error": "job 未完成或已过期"}), 400

    job_result = job.result if isinstance(job.result, dict) else {}
    cat_candidates = job_result.get("category_candidates") if isinstance(job_result.get("category_candidates"), dict) else {}
    loc_candidates = job_result.get("location_candidates") if isinstance(job_result.get("location_candidates"), dict) else {}

    cat_icon_custom = parse_json_object(get_setting("category_icon_map_json", ""), default={})
    loc_icon_custom = parse_json_object(get_setting("location_icon_map_json", ""), default={})

    cat_merged = dict(DEFAULT_CATEGORY_ICON_MAP)
    cat_merged.update(cat_icon_custom)
    loc_merged = dict(DEFAULT_LOCATION_ICON_MAP)
    loc_merged.update(loc_icon_custom)

    applied_categories = 0
    applied_locations = 0
    skipped_categories = 0
    skipped_locations = 0

    def _apply_one(kind: str, name: str, chosen_spec: str) -> bool:
        nonlocal applied_categories, applied_locations, skipped_categories, skipped_locations
        if kind == "category":
            merged = cat_merged
            allowed = cat_candidates.get(name)
        else:
            merged = loc_merged
            allowed = loc_candidates.get(name)

        current_norm = normalize_icon_spec(merged.get(name))
        if current_norm.get("type") != "none":
            if kind == "category":
                skipped_categories += 1
            else:
                skipped_locations += 1
            return False

        if not isinstance(chosen_spec, str):
            return False
        chosen_spec = chosen_spec.strip()
        chosen_norm = normalize_icon_spec(chosen_spec)
        if chosen_norm.get("type") == "none":
            return False

        # Guard: only allow chosen spec from AI candidates list.
        if not isinstance(allowed, list) or not allowed:
            return False
        if chosen_spec not in allowed:
            return False

        merged[name] = chosen_spec
        if kind == "category":
            applied_categories += 1
        else:
            applied_locations += 1
        return True

    if isinstance(sel_categories, dict):
        for k, v in sel_categories.items():
            _apply_one("category", str(k), str(v or ""))
    if isinstance(sel_locations, dict):
        for k, v in sel_locations.items():
            _apply_one("location", str(k), str(v or ""))

    if applied_categories > 0:
        set_setting("category_icon_map_json", dump_json_object(cat_merged))
    if applied_locations > 0:
        set_setting("location_icon_map_json", dump_json_object(loc_merged))

    return jsonify(
        {
            "ok": True,
            "applied": {"categories": applied_categories, "locations": applied_locations},
            "skipped": {"categories": skipped_categories, "locations": skipped_locations},
        }
    )


@bp.post("/settings/notify")
@admin_required
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
    return redirect(url_for("admin.admin_settings", _anchor="sec-notify"))


@bp.post("/settings/integrations")
@admin_required
def settings_save_integrations():
    set_setting("barcode_app_id", (request.form.get("barcode_app_id") or "").strip())
    flash("集成配置已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-integrations"))

@bp.post("/settings/security")
@admin_required
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


@bp.post("/settings/backup-b2")
@admin_required
def settings_save_backup_b2():
    set_setting("backup_b2_endpoint", (request.form.get("backup_b2_endpoint") or "").strip())
    set_setting("backup_b2_bucket_name", (request.form.get("backup_b2_bucket_name") or "").strip())
    set_setting("backup_b2_region", (request.form.get("backup_b2_region") or "us-east-1").strip() or "us-east-1")
    set_setting("backup_source_path", (request.form.get("backup_source_path") or "").strip())
    set_setting("backup_target_key", (request.form.get("backup_target_key") or "latest_backup.csv").strip() or "latest_backup.csv")

    new_access_id = (request.form.get("backup_b2_access_key_id") or "").strip()
    if new_access_id:
        set_setting("backup_b2_access_key_id", new_access_id)
    new_app_key = (request.form.get("backup_b2_application_key") or "").strip()
    if new_app_key:
        set_setting("backup_b2_application_key", new_app_key)

    auto_enabled = (request.form.get("backup_enabled") or "").strip()
    set_setting("backup_enabled", "1" if auto_enabled == "1" else "0")
    set_setting(
        "backup_frequency_cron",
        (request.form.get("backup_frequency_cron") or "").strip() or "0 3 * * *",
    )

    # Reschedule background job immediately (no need to restart container).
    try:
        from ..services.backup_scheduler import reschedule_backup_job

        reschedule_backup_job(current_app)
    except Exception:
        pass

    flash("B2 备份配置已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-backup"))


@bp.post("/settings/ai")
@admin_required
def settings_save_ai():
    flash("该配置已废弃：请使用下方「按能力选择引擎（推荐）」并保存。", "warning")
    return redirect(url_for("admin.admin_settings", _anchor="sec-ai"))


@bp.post("/settings/ai-engines")
@admin_required
def settings_save_ai_engines():
    set_setting("ai_engine_vision_model_id", (request.form.get("ai_engine_vision_model_id") or "").strip())
    set_setting("ai_engine_text_model_id", (request.form.get("ai_engine_text_model_id") or "").strip())
    set_setting("ai_engine_recipes_model_id", (request.form.get("ai_engine_recipes_model_id") or "").strip())
    set_setting("ai_engine_icon_suggest_model_id", (request.form.get("ai_engine_icon_suggest_model_id") or "").strip())
    flash("能力引擎选择已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-ai"))


# Items backup/export (CSV)
@bp.get("/items/export")
@admin_required
def items_export():
    """导出所有库存 Item 为 CSV 备份文件（包含软删除/用完记录）。"""
    items = Item.query.order_by(Item.created_at.asc(), Item.id.asc()).all()

    headers = [
        "name",
        "category",
        "quantity",
        "unit",
        "location",
        "note",
        "barcode",
        "shelf_life_days",
        "used_up",
        "created_at",
        "updated_at",
        "deleted_at",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for it in items:
        writer.writerow(
            {
                "name": (it.name or "").strip(),
                "category": (it.category or "").strip(),
                "quantity": float(it.quantity or 0.0),
                "unit": (it.unit or "").strip(),
                "location": (it.location or "").strip(),
                "note": (it.note or "").strip(),
                "barcode": (it.barcode or "").strip() if it.barcode else "",
                "shelf_life_days": it.shelf_life_days if it.shelf_life_days is not None else "",
                "used_up": 1 if bool(it.used_up) else 0,
                "created_at": it.created_at.isoformat() if it.created_at else "",
                "updated_at": it.updated_at.isoformat() if it.updated_at else "",
                "deleted_at": it.deleted_at.isoformat() if it.deleted_at else "",
            }
        )

    # Important: add UTF-8 BOM so Excel/WPS opens as UTF-8 (avoid mojibake).
    csv_bytes = buf.getvalue().encode("utf-8-sig")
    filename = f"items-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"

    resp = current_app.response_class(csv_bytes, mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@bp.post("/items/import")
@admin_required
def items_import():
    """
    从 CSV 备份导入库存 Item。
    仅做“追加导入”：不会删除或覆盖现有记录，适合灾备恢复或跨实例迁移。
    """
    f = request.files.get("items_file")
    if not f or not f.filename:
        flash("请选择要导入的 CSV 备份文件。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-backup"))

    try:
        raw_bytes = f.read()
    except Exception:
        try:
            raw_bytes = f.read()
        except Exception:
            flash("导入失败：无法读取文件内容。", "danger")
            return redirect(url_for("admin.admin_settings", _anchor="sec-backup"))

    try:
        text = raw_bytes.decode("utf-8-sig")
    except Exception:
        try:
            text = raw_bytes.decode("utf-8", errors="ignore")
        except Exception:
            flash("导入失败：无法解码 CSV 文件。", "danger")
            return redirect(url_for("admin.admin_settings", _anchor="sec-backup"))

    # Backward compatibility: legacy JSON backups
    trimmed = (text or "").lstrip()
    if trimmed.startswith("{") or trimmed.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                items_data = data.get("items")
            else:
                items_data = data

            if isinstance(items_data, list):
                imported = 0
                now = datetime.utcnow()

                def _parse_dt_json(v: Any) -> datetime | None:
                    if not v:
                        return None
                    try:
                        s = str(v).strip()
                        if s.endswith("Z"):
                            s = s[:-1]
                        return datetime.fromisoformat(s)
                    except Exception:
                        return None

                for obj in items_data:
                    if not isinstance(obj, dict):
                        continue
                    name = str(obj.get("name") or "").strip()
                    if not name:
                        continue

                    category = str(obj.get("category") or "其他").strip() or "其他"
                    unit = str(obj.get("unit") or "份").strip() or "份"
                    location = str(obj.get("location") or "冰箱").strip() or "冰箱"
                    note = str(obj.get("note") or "").strip()
                    barcode = str(obj.get("barcode") or "").strip() or None

                    try:
                        quantity = float(obj.get("quantity") or 0.0)
                    except Exception:
                        quantity = 0.0
                    if quantity < 0:
                        quantity = 0.0

                    shelf_life_days = obj.get("shelf_life_days")
                    try:
                        shelf_life_days_int = (
                            int(shelf_life_days)
                            if shelf_life_days not in (None, "", "null")
                            else None
                        )
                    except Exception:
                        shelf_life_days_int = None

                    used_up = bool(obj.get("used_up", False))
                    created_at = _parse_dt_json(obj.get("created_at")) or now
                    updated_at = _parse_dt_json(obj.get("updated_at")) or created_at
                    deleted_at = _parse_dt_json(obj.get("deleted_at"))

                    it = Item(
                        name=name,
                        category=category,
                        quantity=quantity,
                        unit=unit,
                        location=location,
                        note=note,
                        barcode=barcode,
                        shelf_life_days=shelf_life_days_int,
                        used_up=used_up,
                        deleted_at=deleted_at,
                    )
                    it.created_at = created_at
                    it.updated_at = updated_at
                    db.session.add(it)
                    imported += 1

                if imported > 0:
                    db.session.commit()
                    flash(f"已导入 {imported} 条库存记录（追加导入，未删除现有数据）。", "success")
                else:
                    flash("导入完成：未发现有效的库存记录。", "warning")
                return redirect(url_for("admin.admin_settings", _anchor="sec-backup"))
        except Exception:
            # Fall back to CSV parsing
            pass

    try:
        sample = text[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    except Exception:
        flash("导入失败：CSV 解析失败。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-backup"))

    def _parse_dt(v: Any) -> datetime | None:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        try:
            # best-effort: drop timezone info to keep SQLite happy
            if s.endswith("Z"):
                s = s[:-1]
            return datetime.fromisoformat(s)
        except Exception:
            return None

    def _parse_used_up(v: Any) -> bool:
        s = str(v).strip().lower()
        if s in {"1", "true", "yes", "y"}:
            return True
        return False

    def _to_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(str(v).strip())
        except Exception:
            return default

    imported = 0
    now = datetime.utcnow()

    for row in reader:
        if not isinstance(row, dict):
            continue
        name = str((row.get("name") or "")).strip()
        if not name:
            continue

        category = str((row.get("category") or "其他")).strip() or "其他"
        unit = str((row.get("unit") or "份")).strip() or "份"
        location = str((row.get("location") or "冰箱")).strip() or "冰箱"
        note = str((row.get("note") or "")).strip()
        barcode_raw = str((row.get("barcode") or "")).strip()
        barcode = barcode_raw if barcode_raw else None

        quantity = _to_float(row.get("quantity") or 0.0, default=0.0)
        if quantity < 0:
            quantity = 0.0

        shelf_raw = row.get("shelf_life_days")
        if shelf_raw is None:
            shelf_life_days_int = None
        else:
            s = str(shelf_raw).strip()
            try:
                shelf_life_days_int = int(s) if s and s != "null" else None
            except Exception:
                shelf_life_days_int = None

        used_up = _parse_used_up(row.get("used_up"))

        created_at = _parse_dt(row.get("created_at")) or now
        updated_at = _parse_dt(row.get("updated_at")) or created_at
        deleted_at = _parse_dt(row.get("deleted_at"))

        it = Item(
            name=name,
            category=category,
            quantity=quantity,
            unit=unit,
            location=location,
            note=note,
            barcode=barcode,
            shelf_life_days=shelf_life_days_int,
            used_up=used_up,
            deleted_at=deleted_at,
        )
        it.created_at = created_at
        it.updated_at = updated_at
        db.session.add(it)
        imported += 1

    if imported > 0:
        db.session.commit()
        flash(f"已导入 {imported} 条库存记录（追加导入，未删除现有数据）。", "success")
    else:
        flash("导入完成：未发现有效的库存记录。", "warning")

    return redirect(url_for("admin.admin_settings", _anchor="sec-backup"))


def _duplicate_key(name: str, category: str, location: str) -> tuple[str, str, str]:
    """
    Duplicate key normalization:
    - category/location fallback to app defaults (avoid null/empty mismatch)
    - unicode NFKC normalization to reduce "visually same but bytes different" issues
    - collapse all whitespace
    """
    import unicodedata

    def _norm(v: str | None, default: str) -> str:
        s = (v or "").strip()
        # NFKC: unify full/half-width & compatibility forms
        s = unicodedata.normalize("NFKC", s)
        # collapse whitespace
        s = " ".join(s.split())
        return s if s else default

    return (_norm(name, ""), _norm(category, "其他"), _norm(location, "冰箱"))


@bp.post("/items/duplicates/preview")
@admin_required
def items_duplicates_preview():
    """
    预览重复项（仅针对未删除 items）。
    重复定义：name + category + location 三者完全一致（去两端空格）。
    规则：每组只保留最小 id，其余进入回收站（soft delete）。
    """
    rows = (
        Item.query.with_entities(Item.id, Item.name, Item.category, Item.location)
        .filter(Item.deleted_at.is_(None))
        .order_by(Item.name.asc(), Item.category.asc(), Item.location.asc(), Item.id.asc())
        .all()
    )
    before_total = len(rows)

    first_id_by_key: dict[tuple[str, str, str], int] = {}
    dup_count_by_key: dict[tuple[str, str, str], int] = {}
    to_delete_ids: list[int] = []

    for item_id, name, category, location in rows:
        k = _duplicate_key(name, category, location)
        dup_count_by_key[k] = dup_count_by_key.get(k, 0) + 1
        if k not in first_id_by_key:
            first_id_by_key[k] = item_id
        else:
            to_delete_ids.append(item_id)

    to_delete_count = len(to_delete_ids)
    after_total_preview = max(0, before_total - to_delete_count)

    dup_groups = []
    for k, c in dup_count_by_key.items():
        if not (c and c > 1):
            continue
        dup_groups.append(
            {
                "name": k[0],
                "category": k[1],
                "location": k[2],
                "count": c,
                "keep_id": first_id_by_key.get(k),
                "delete_count": c - 1,
            }
        )
    dup_groups.sort(key=lambda x: (-x["count"], x["name"], x["category"], x["location"]))

    return {
        "success": True,
        "before_total": before_total,
        "to_delete_count": to_delete_count,
        "after_total_preview": after_total_preview,
        "dup_groups": dup_groups[:20],
        "kept_strategy": "每组保留最小 id，其余移入回收站",
    }


@bp.post("/items/duplicates/cleanup")
@admin_required
def items_duplicates_cleanup():
    """
    一键清理重复项（soft delete）。
    同预览逻辑：name + category + location 完全一致（去两端空格），保留每组最小 id，其余 deleted_at=now。
    """
    rows = (
        Item.query.with_entities(Item.id, Item.name, Item.category, Item.location)
        .filter(Item.deleted_at.is_(None))
        .order_by(Item.name.asc(), Item.category.asc(), Item.location.asc(), Item.id.asc())
        .all()
    )
    before_total = len(rows)

    first_id_by_key: dict[tuple[str, str, str], int] = {}
    to_delete_ids: list[int] = []

    for item_id, name, category, location in rows:
        k = _duplicate_key(name, category, location)
        if k not in first_id_by_key:
            first_id_by_key[k] = item_id
        else:
            to_delete_ids.append(item_id)

    if not to_delete_ids:
        after_total_actual = before_total
        return {
            "success": True,
            "before_total": before_total,
            "to_delete_count": 0,
            "after_total_actual": after_total_actual,
            "dup_groups": [],
            "message": "未发现重复项",
        }

    now = datetime.utcnow()
    # Soft delete duplicates: move to recycle bin.
    (
        Item.query.filter(Item.id.in_(to_delete_ids))
        .update({Item.deleted_at: now, Item.updated_at: now}, synchronize_session=False)
    )
    db.session.commit()

    after_total_actual = Item.query.filter(Item.deleted_at.is_(None)).count()
    return {
        "success": True,
        "before_total": before_total,
        "to_delete_count": len(to_delete_ids),
        "after_total_actual": int(after_total_actual),
        "message": "重复项清理完成（已移入回收站）",
    }


# Backward-compatible endpoint (kept, but UI no longer posts here)
@bp.post("/settings")
@admin_required
def admin_settings_save():
    flash("设置页已升级为“分模块保存”。请在对应模块内点击保存按钮。", "warning")
    return redirect(url_for("admin.admin_settings"))


@bp.post("/send-digest")
@admin_required
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
@admin_required
def admin_email_history():
    logs = EmailLog.query.order_by(EmailLog.id.desc()).limit(50).all()
    return render_template("admin_email_history.html", logs=logs)

