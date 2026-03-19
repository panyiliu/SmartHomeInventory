from __future__ import annotations

import os
from typing import Any

import requests

from .settings_service import get_setting


def barcode_lookup(barcode: str) -> tuple[bool, str, dict[str, Any]]:
    barcode = (barcode or "").strip()
    if not barcode:
        return False, "barcode 不能为空。", {}

    app_id = get_setting("barcode_app_id", "").strip()
    app_secret = os.getenv("FRIDGE_BARCODE_APP_SECRET") or get_setting("barcode_app_secret", "")
    app_secret = (app_secret or "").strip()
    if not app_id or not app_secret:
        return False, "条码接口未配置（需要 barcode_app_id 和 barcode_app_secret/环境变量）。", {}

    url = "https://www.mxnzp.com/api/barcode/goods/details"
    params = {"barcode": barcode, "app_id": app_id, "app_secret": app_secret}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return False, f"请求失败：{e}", {}

    if not isinstance(data, dict):
        return False, "接口返回异常。", {}
    if data.get("code") != 1:
        return False, str(data.get("msg") or "查询失败"), {}

    goods = data.get("data") or {}
    return True, "", goods

