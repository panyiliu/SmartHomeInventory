from __future__ import annotations

import os
import json
import re
from datetime import datetime

from ..extensions import db
from ..models import Setting


def get_setting(key: str, default: str = "") -> str:
    row = db.session.get(Setting, key)
    return row.value if row else default


def set_setting(key: str, value: str) -> None:
    row = db.session.get(Setting, key)
    if not row:
        row = Setting(key=key, value=value, updated_at=datetime.utcnow())
        db.session.add(row)
    else:
        row.value = value
        row.updated_at = datetime.utcnow()
    db.session.commit()


def safe_int(v: str | None, default: int = 0) -> int:
    try:
        if v is None:
            return default
        s = str(v).strip()
        if s == "":
            return default
        return int(float(s))
    except Exception:
        return default


def get_int_setting(key: str, default: int) -> int:
    raw = get_setting(key, str(default)).strip()
    try:
        v = int(raw)
    except ValueError:
        v = default
    return v if v >= 0 else default


def parse_emails(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.replace(";", ",").replace("\n", ",").split(",")]
    return [p for p in parts if p]


def get_secret_setting(*, setting_key: str, env_key: str) -> str:
    """
    Read secrets with env override.
    Priority:
    1) environment variable (recommended)
    2) settings table (local-only convenience)
    """
    v = (os.environ.get(env_key) or "").strip()
    if v:
        return v
    return get_setting(setting_key, "").strip()


def parse_option_list(raw: str, *, default: list[str]) -> list[str]:
    """
    Parse option lists stored in settings.

    New format (preferred): JSON array string, e.g. ["冰箱","冷藏"]
    Legacy format: comma-separated string, e.g. 冰箱,冷藏
    """
    s = (raw or "").strip()
    if not s:
        return list(default)
    if s.startswith("["):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                out = []
                for x in arr:
                    t = str(x).strip()
                    if t and t not in out:
                        out.append(t)
                return out if out else list(default)
        except Exception:
            pass
    parts = [p.strip() for p in s.split(",")]
    out = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return out if out else list(default)


def dump_option_list(items: list[str]) -> str:
    """
    Store option lists as JSON arrays (no comma-joined strings).
    """
    out: list[str] = []
    for x in items or []:
        t = str(x).strip()
        if t and t not in out:
            out.append(t)
    return json.dumps(out, ensure_ascii=False)


def parse_json_object(raw: str, *, default: dict) -> dict:
    s = (raw or "").strip()
    if not s:
        return dict(default or {})
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return dict(default or {})


def dump_json_object(obj: dict) -> str:
    if not isinstance(obj, dict):
        obj = {}
    return json.dumps(obj, ensure_ascii=False, indent=2)


_BI_NAME_RE = re.compile(r"^[a-z0-9-]+$")


def normalize_icon_spec(spec: str | None) -> dict:
    """
    Icon spec formats (stored in settings):
    - "emoji:🥬" or "🥬"
    - "bi:trash" (Bootstrap Icons name, without "bi-")
    - "svg:depleted" (built-in SVG key)
    """
    s = str(spec or "").strip()
    if not s:
        return {"type": "none"}
    if s.startswith("emoji:"):
        t = s[len("emoji:") :].strip()
        return {"type": "emoji", "text": t} if t else {"type": "none"}
    if s.startswith("bi:"):
        name = s[len("bi:") :].strip()
        if _BI_NAME_RE.match(name or ""):
            return {"type": "bi", "name": name}
        return {"type": "none"}
    if s.startswith("svg:"):
        key = s[len("svg:") :].strip()
        return {"type": "svg", "key": key} if key else {"type": "none"}
    # Plain string: treat as emoji/text.
    return {"type": "emoji", "text": s}


def get_category_icon_map_normalized() -> dict[str, dict]:
    raw = get_setting("category_icon_map_json", "")
    obj = parse_json_object(raw, default={})
    out: dict[str, dict] = {}
    for k, v in obj.items():
        key = str(k).strip()
        if not key:
            continue
        out[key] = normalize_icon_spec(str(v))
    return out


def get_location_icon_map_normalized() -> dict[str, dict]:
    raw = get_setting("location_icon_map_json", "")
    obj = parse_json_object(raw, default={})
    out: dict[str, dict] = {}
    for k, v in obj.items():
        key = str(k).strip()
        if not key:
            continue
        out[key] = normalize_icon_spec(str(v))
    return out


def get_category_short_label_map() -> dict[str, str]:
    raw = get_setting("category_label_map_json", "")
    obj = parse_json_object(raw, default={})
    out: dict[str, str] = {}
    for k, v in obj.items():
        key = str(k).strip()
        val = str(v).strip()
        if key and val:
            out[key] = val
    return out


def get_location_short_label_map() -> dict[str, str]:
    raw = get_setting("location_label_map_json", "")
    obj = parse_json_object(raw, default={})
    out: dict[str, str] = {}
    for k, v in obj.items():
        key = str(k).strip()
        val = str(v).strip()
        if key and val:
            out[key] = val
    return out


def get_category_options() -> list[str]:
    default_categories = ["蔬菜", "水果", "肉类", "海鲜", "蛋奶", "主食", "调料", "饮料", "零食", "其他"]
    return parse_option_list(get_setting("category_options", ""), default=default_categories)


def get_location_options() -> list[str]:
    default_locations = ["冰箱", "冷藏", "冷冻", "常温", "橱柜", "厨房", "室外", "卫生间"]
    return parse_option_list(get_setting("location_options", ""), default=default_locations)

