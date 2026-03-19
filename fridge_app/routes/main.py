from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..extensions import db
from ..models import Item
from ..services.items_service import list_items
from ..services.settings_service import (
    get_category_icon_map_normalized,
    get_category_short_label_map,
    get_int_setting,
    get_location_icon_map_normalized,
    get_location_short_label_map,
    get_setting,
    parse_option_list,
    get_secret_setting,
)


bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    location = (request.args.get("location") or "").strip()
    view = (request.args.get("view") or "all").strip()
    sort = (request.args.get("sort") or "created_desc").strip()

    if view == "trash":
        u = getattr(g, "current_user", None)
        if not (u and getattr(u, "is_admin", lambda: False)()):
            abort(403)

    expiring_soon_days = get_int_setting("expiring_soon_days", 3)
    category_icon_map = get_category_icon_map_normalized()
    location_icon_map = get_location_icon_map_normalized()
    category_label_map = get_category_short_label_map()
    location_label_map = get_location_short_label_map()
    items, categories, locations, location_pills, category_pills = list_items(
        q=q,
        category=category,
        location=location,
        view=view,
        sort=sort,
        expiring_soon_days=expiring_soon_days,
    )

    return render_template(
        "index.html",
        items=items,
        q=q,
        category=category,
        location=location,
        view=view,
        sort=sort,
        expiring_soon_days=expiring_soon_days,
        categories=categories,
        locations=locations,
        location_pills=location_pills,
        category_pills=category_pills,
        category_icon_map=category_icon_map,
        location_icon_map=location_icon_map,
        category_label_map=category_label_map,
        location_label_map=location_label_map,
    )


@bp.route("/add", methods=["GET", "POST"])
def add():
    today_str = datetime.utcnow().date().isoformat()
    category_opts_raw = get_setting("category_options", "")
    location_opts_raw = get_setting("location_options", "")
    category_opts = parse_option_list(
        category_opts_raw,
        default=["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "其他"],
    )
    location_opts = parse_option_list(
        location_opts_raw,
        default=["冰箱", "冷藏", "冷冻", "常温", "橱柜", "厨房", "室外", "卫生间"],
    )

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "其他").strip() or "其他"
        unit = (request.form.get("unit") or "份").strip() or "份"
        location = (request.form.get("location") or "冰箱").strip() or "冰箱"
        note = (request.form.get("note") or "").strip()
        shelf_life_raw = (request.form.get("shelf_life_days") or "").strip()
        record_date_raw = (request.form.get("record_date") or "").strip()

        shelf_life_days: int | None
        if not shelf_life_raw:
            shelf_life_days = None
        else:
            try:
                shelf_life_days = int(shelf_life_raw)
            except ValueError:
                shelf_life_days = -1

        try:
            quantity = float((request.form.get("quantity") or "0").strip())
        except ValueError:
            quantity = -1

        if not name:
            flash("名称不能为空。", "danger")
        elif quantity < 0:
            flash("数量必须是一个不小于 0 的数字。", "danger")
        elif shelf_life_days is not None and shelf_life_days < 0:
            flash("保质期必须是一个不小于 0 的整数（天），或留空。", "danger")
        else:
            if record_date_raw:
                try:
                    rec_date = datetime.strptime(record_date_raw, "%Y-%m-%d").date()
                except ValueError:
                    rec_date = datetime.utcnow().date()
            else:
                rec_date = datetime.utcnow().date()
            created_at = datetime.combine(rec_date, datetime.utcnow().time())

            item = Item(
                name=name,
                category=category,
                quantity=quantity,
                unit=unit,
                location=location,
                note=note,
                shelf_life_days=shelf_life_days,
            )
            item.created_at = created_at
            item.touch()
            db.session.add(item)
            db.session.commit()
            flash(f"已添加：{name}", "success")
            return redirect(url_for("main.index"))

    ai_text_enabled = bool(get_secret_setting(setting_key="volcengine_api_key", env_key="VOLCENGINE_API_KEY"))

    return render_template(
        "add.html",
        today=today_str,
        category_options=category_opts,
        location_options=location_opts,
        ai_text_enabled=ai_text_enabled,
    )


@bp.route("/item/<int:item_id>/edit", methods=["GET", "POST"])
def edit(item_id: int):
    item = Item.query.get_or_404(item_id)
    category_opts_raw = get_setting("category_options", "")
    location_opts_raw = get_setting("location_options", "")
    category_opts = parse_option_list(
        category_opts_raw,
        default=["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "其他"],
    )
    location_opts = parse_option_list(
        location_opts_raw,
        default=["冰箱", "冷藏", "冷冻", "常温", "橱柜", "厨房", "室外", "卫生间"],
    )

    next_url = (request.args.get("next") or request.form.get("next") or "").strip()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "其他").strip() or "其他"
        unit = (request.form.get("unit") or "份").strip() or "份"
        location = (request.form.get("location") or "冰箱").strip() or "冰箱"
        note = (request.form.get("note") or "").strip()
        shelf_life_raw = (request.form.get("shelf_life_days") or "").strip()
        record_date_raw = (request.form.get("record_date") or "").strip()

        shelf_life_days: int | None
        if not shelf_life_raw:
            shelf_life_days = None
        else:
            try:
                shelf_life_days = int(shelf_life_raw)
            except ValueError:
                shelf_life_days = -1

        try:
            quantity = float((request.form.get("quantity") or "0").strip())
        except ValueError:
            quantity = -1

        if not name:
            flash("名称不能为空。", "danger")
        elif quantity < 0:
            flash("数量必须是一个不小于 0 的数字。", "danger")
        elif shelf_life_days is not None and shelf_life_days < 0:
            flash("保质期必须是一个不小于 0 的整数（天），或留空。", "danger")
        else:
            if record_date_raw:
                try:
                    rec_date = datetime.strptime(record_date_raw, "%Y-%m-%d").date()
                except ValueError:
                    rec_date = item.created_at.date()
            else:
                rec_date = item.created_at.date()
            item.created_at = datetime.combine(rec_date, item.created_at.time())

            item.name = name
            item.category = category
            item.quantity = quantity
            item.unit = unit
            item.location = location
            item.note = note
            item.shelf_life_days = shelf_life_days
            item.touch()
            db.session.commit()
            flash("已保存修改。", "success")
            if next_url.startswith("/"):
                return redirect(next_url)
            return redirect(url_for("main.index"))

    return render_template(
        "edit.html",
        item=item,
        category_options=category_opts,
        location_options=location_opts,
        next_url=next_url,
    )

