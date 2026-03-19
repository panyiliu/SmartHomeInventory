from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for, current_app

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
from ..utils.auth import admin_required


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
    return redirect(url_for("admin.admin_settings", _anchor="sec-notify-ai"))


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
    flash("能力引擎选择已保存。", "success")
    return redirect(url_for("admin.admin_settings", _anchor="sec-ai"))


# Items backup/export (JSON)
@bp.get("/items/export")
@admin_required
def items_export():
    """导出所有库存 Item 为 JSON 备份文件（包含软删除/用完记录）。"""
    items = Item.query.order_by(Item.created_at.asc(), Item.id.asc()).all()

    def _item_to_dict(it: Item) -> dict[str, Any]:
        return {
            "name": (it.name or "").strip(),
            "category": (it.category or "").strip(),
            "quantity": float(it.quantity or 0.0),
            "unit": (it.unit or "").strip(),
            "location": (it.location or "").strip(),
            "note": (it.note or "").strip(),
            "barcode": (it.barcode or "").strip() if it.barcode else "",
            "shelf_life_days": it.shelf_life_days,
            "used_up": bool(it.used_up),
            "created_at": it.created_at.isoformat() if it.created_at else None,
            "updated_at": it.updated_at.isoformat() if it.updated_at else None,
            "deleted_at": it.deleted_at.isoformat() if it.deleted_at else None,
        }

    payload = {
        "version": 1,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "items": [_item_to_dict(it) for it in items],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    filename = f"items-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"

    resp = current_app.response_class(text, mimetype="application/json; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@bp.post("/items/import")
@admin_required
def items_import():
    """
    从 JSON 备份导入库存 Item。
    仅做“追加导入”：不会删除或覆盖现有记录，适合灾备恢复或跨实例迁移。
    """
    f = request.files.get("items_file")
    if not f or not f.filename:
        flash("请选择要导入的 JSON 备份文件。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-data"))

    try:
        raw = f.read().decode("utf-8")
    except Exception:
        try:
            raw = f.read().decode("utf-8", errors="ignore")
        except Exception:
            flash("导入失败：无法读取文件内容。", "danger")
            return redirect(url_for("admin.admin_settings", _anchor="sec-data"))

    try:
        data = json.loads(raw)
    except Exception:
        flash("导入失败：JSON 解析错误。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-data"))

    # 兼容两种结构：纯列表 或 包含 items 字段的对象
    if isinstance(data, dict):
        items_data = data.get("items")
    else:
        items_data = data

    if not isinstance(items_data, list):
        flash("导入失败：JSON 结构不正确，应为 items 列表。", "danger")
        return redirect(url_for("admin.admin_settings", _anchor="sec-data"))

    imported = 0
    now = datetime.utcnow()

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
            shelf_life_days_int = int(shelf_life_days) if shelf_life_days not in (None, "", "null") else None
        except Exception:
            shelf_life_days_int = None

        used_up = bool(obj.get("used_up", False))

        def _parse_dt(v: Any) -> datetime | None:
            if not v:
                return None
            try:
                s = str(v).strip()
                # best-effort: drop timezone info to keep SQLite happy
                if s.endswith("Z"):
                    s = s[:-1]
                # strip microseconds if present with 'Z' style
                return datetime.fromisoformat(s)
            except Exception:
                return None

        created_at = _parse_dt(obj.get("created_at")) or now
        updated_at = _parse_dt(obj.get("updated_at")) or created_at
        deleted_at = _parse_dt(obj.get("deleted_at"))

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

    return redirect(url_for("admin.admin_settings", _anchor="sec-data"))


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

