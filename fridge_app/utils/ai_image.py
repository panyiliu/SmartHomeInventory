from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .doubao_core import ItemResult, call_ark_vision
from ..services.settings_service import get_category_options, get_location_options


def recognize_foods_from_image(image_bytes: bytes) -> list[dict[str, Any]]:
    if not image_bytes:
        raise ValueError("empty image")

    raw = call_ark_vision(image_bytes)
    allowed_categories = get_category_options()
    if "其他" not in allowed_categories:
        allowed_categories.append("其他")
    allowed_locations = get_location_options()

    def norm_one(obj: Any) -> dict[str, Any]:
        if not isinstance(obj, dict):
            return {"name": "", "quantity": 0, "category": "", "location": "", "status": ""}
        name = (obj.get("name") or "").strip()
        qty = obj.get("number", 0)
        cat = (obj.get("category") or "").strip()
        loc = (obj.get("location") or "").strip()
        status = (obj.get("status") or "").strip()

        def safe_int(v: Any) -> int:
            try:
                s = str(v).strip()
                if s == "":
                    return 0
                return int(float(s.replace(",", "")))
            except Exception:
                return 0

        # strict enforcement: only from configured options
        if cat and cat not in allowed_categories:
            cat = "其他"
        if loc and loc not in allowed_locations:
            loc = ""

        return {
            "name": name,
            "quantity": max(0, safe_int(qty)),
            "category": cat,
            "location": loc,
            "status": status,
        }

    if isinstance(raw, dict) and ("物品名称" in raw or "数量" in raw or "类型" in raw):
        item = ItemResult.from_ai(raw)
        d = asdict(item)
        return [
            {
                "name": (d.get("物品名称") or "").strip(),
                "quantity": max(0, int(d.get("数量") or 0)),
                "category": (d.get("类型") or "").strip(),
                "location": "",
                "status": "",
            }
        ]

    if isinstance(raw, list):
        return [norm_one(x) for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [norm_one(raw)]
    return []


def recognize_food_from_image(image_bytes: bytes) -> dict[str, Any]:
    items = recognize_foods_from_image(image_bytes)
    return items[0] if items else {"name": "", "quantity": 0, "category": "", "location": "", "status": ""}

