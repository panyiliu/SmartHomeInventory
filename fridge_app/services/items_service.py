from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_

from ..extensions import db
from ..models import Item
from .settings_service import get_setting, parse_option_list


def list_items(
    *,
    q: str,
    category: str,
    location: str,
    view: str,
    sort: str,
    expiring_soon_days: int,
) -> tuple[list[Item], list[str], list[str], list[dict[str, Any]]]:
    query = Item.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Item.name.ilike(like), Item.note.ilike(like), Item.unit.ilike(like)))

    base_items = query.order_by(Item.created_at.desc(), Item.id.desc()).all()
    now = datetime.utcnow()

    def severity(it: Item) -> int:
        remain = it.remaining_days(now=now)
        if remain is None:
            return 3
        if remain < 0:
            return 0
        if remain <= expiring_soon_days:
            return 1
        return 2

    def view_match(it: Item) -> bool:
        remain = it.remaining_days(now=now)
        if view == "usedup":
            return bool(it.used_up)
        if bool(it.used_up):
            return False
        if view == "expired":
            return remain is not None and remain < 0
        if view == "expiring":
            return remain is not None and 0 <= remain <= expiring_soon_days
        return True

    base_items = [it for it in base_items if view_match(it)]

    # Category pills should depend on current location filter (but not category filter)
    if location:
        scope_for_categories = [it for it in base_items if (it.location or "").strip() == location]
    else:
        scope_for_categories = list(base_items)
    cat_counts: dict[str, int] = {}
    for it in scope_for_categories:
        key = (it.category or "").strip() or "其他"
        cat_counts[key] = cat_counts.get(key, 0) + 1
    category_pills = [{"name": k, "count": v} for k, v in sorted(cat_counts.items(), key=lambda x: x[0])]

    loc_counts: dict[str, int] = {}
    for it in base_items:
        key = (it.location or "").strip() or "（未设置）"
        loc_counts[key] = loc_counts.get(key, 0) + 1
    location_pills = [{"name": k, "count": v} for k, v in sorted(loc_counts.items(), key=lambda x: x[0])]

    if location:
        items = [it for it in base_items if (it.location or "").strip() == location]
    else:
        items = list(base_items)

    if category:
        items = [it for it in items if (it.category or "").strip() == category]

    if sort == "name_asc":
        items.sort(key=lambda it: ((it.name or "").lower(), -it.created_at.timestamp(), -it.id))
    elif sort == "category_asc":
        items.sort(
            key=lambda it: (
                (it.category or "").lower(),
                (it.name or "").lower(),
                -it.created_at.timestamp(),
                -it.id,
            )
        )
    elif sort == "location_asc":
        items.sort(
            key=lambda it: (
                (it.location or "").lower(),
                (it.name or "").lower(),
                -it.created_at.timestamp(),
                -it.id,
            )
        )
    elif sort == "remain_asc":
        items.sort(
            key=lambda it: (
                1 if it.remaining_days(now=now) is None else 0,
                it.remaining_days(now=now) if it.remaining_days(now=now) is not None else 10**9,
                -it.created_at.timestamp(),
                -it.id,
            )
        )
    elif sort == "stored_desc":
        items.sort(key=lambda it: (-it.stored_days(now=now), -it.created_at.timestamp(), -it.id))
    elif sort == "smart":
        items.sort(key=lambda it: (severity(it), -it.created_at.timestamp(), -it.id))
    else:
        items.sort(key=lambda it: (-it.created_at.timestamp(), -it.id))

    # Filter option lists should follow admin settings, not historical DB distinct values.
    categories = parse_option_list(
        get_setting("category_options", ""),
        default=["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "其他"],
    )
    locations = parse_option_list(
        get_setting("location_options", ""),
        default=["冰箱", "冷藏", "冷冻", "常温", "橱柜", "厨房", "室外", "卫生间"],
    )
    return items, categories, locations, location_pills, category_pills


def adjust_quantity(item: Item, delta: float) -> Item:
    new_qty = item.quantity + delta
    if new_qty < 0:
        new_qty = 0.0
    item.quantity = new_qty
    if item.quantity > 0:
        item.used_up = False
    item.touch()
    db.session.commit()
    return item


def mark_used_up(item: Item) -> Item:
    item.used_up = True
    item.touch()
    db.session.commit()
    return item


def use_up(item: Item) -> Item:
    item.quantity = 0.0
    item.used_up = True
    item.touch()
    db.session.commit()
    return item

