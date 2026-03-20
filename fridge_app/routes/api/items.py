from __future__ import annotations

import os
import traceback

from flask import Blueprint, current_app, jsonify, request

from ...extensions import db
from ...models import Item
from ...services.barcode_service import barcode_lookup
from ...services.settings_service import safe_int
from ...utils.ai_image import recognize_foods_from_image
from ...utils.ai_text import extract_items_from_text
from ...services.ai_job_service import ai_job_service


bp = Blueprint("items_api", __name__)


@bp.get("/api/suggest")
def api_suggest():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    rows = Item.query.filter(Item.name.ilike(like)).order_by(Item.updated_at.desc()).limit(8).all()
    return jsonify(
        [
            {
                "id": r.id,
                "name": r.name,
                "category": r.category,
                "unit": r.unit,
                "location": r.location,
                "shelf_life_days": r.shelf_life_days,
            }
            for r in rows
        ]
    )


@bp.get("/api/barcode/lookup")
def api_barcode_lookup():
    barcode = (request.args.get("barcode") or "").strip()
    ok, err, goods = barcode_lookup(barcode)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify(
        {
            "ok": True,
            "barcode": goods.get("barcode") or barcode,
            "goodsName": goods.get("goodsName") or "",
            "brand": goods.get("brand") or "",
            "standard": goods.get("standard") or "",
            "supplier": goods.get("supplier") or "",
            "price": goods.get("price") or "",
        }
    )


@bp.post("/api/barcode/scan-add")
def api_barcode_scan_add():
    payload = request.get_json(silent=True) or {}
    barcode = (payload.get("barcode") or "").strip()
    ok, err, goods = barcode_lookup(barcode)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400

    goods_name = (goods.get("goodsName") or "").strip()
    if not goods_name:
        return jsonify({"ok": False, "error": "未获取到商品名称。"}), 400

    existing = Item.query.filter(Item.barcode == barcode, Item.deleted_at.is_(None)).order_by(Item.id.desc()).first()
    if existing:
        existing.quantity = float(existing.quantity) + 1.0
        existing.touch()
        db.session.commit()
        return jsonify(
            {
                "ok": True,
                "action": "incremented",
                "item_id": existing.id,
                "name": existing.name,
                "barcode": barcode,
                "quantity": existing.quantity,
            }
        )

    item = Item(
        name=goods_name,
        category="其他",
        quantity=1.0,
        unit="个",
        location="冰箱",
        note="",
        barcode=barcode,
    )
    item.touch()
    db.session.add(item)
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "action": "created",
            "item_id": item.id,
            "name": item.name,
            "barcode": barcode,
            "quantity": item.quantity,
        }
    )


@bp.post("/api/ai/recognize-food")
def api_ai_recognize_food():
    f = request.files.get("image")
    if not f:
        return jsonify({"ok": False, "error": "缺少图片"}), 400

    async_flag_raw = (request.form.get("async") or request.args.get("async") or "").strip().lower()
    async_flag = async_flag_raw in {"1", "true", "yes", "on"}
    try:
        img = f.read()
        if not img:
            return jsonify({"ok": False, "error": "缺少图片"}), 400

        if async_flag:
            job_id = ai_job_service.create_job(
                kind="ai_recognize_food",
                fn=lambda: recognize_foods_from_image(img),
                meta={"image_size": len(img)},
                app=current_app._get_current_object(),
            )
            return jsonify({"ok": True, "async": True, "job_id": job_id})

        items = recognize_foods_from_image(img)
        return jsonify({"ok": True, "data": items})
    except RuntimeError as e:
        msg = str(e) or "AI 配置缺失"
        if "VOLCENGINE_API_KEY" in msg:
            return jsonify({"ok": False, "error": "AI 未配置：请设置环境变量 VOLCENGINE_API_KEY，或到「设置」页保存 VOLCENGINE_API_KEY。"}), 400
        return jsonify({"ok": False, "error": msg}), 400
    except Exception:
        verbose_raw = (os.environ.get("AI_LOG_VERBOSE") or "1").strip()
        verbose = verbose_raw in {"1", "true", "True", "YES", "yes", "on", "ON"}
        if verbose:
            print("=" * 80)
            print("[AI] /api/ai/recognize-food failed")
            print("filename:", getattr(f, "filename", ""))
            try:
                print("content_type:", getattr(f, "content_type", ""))
            except Exception:
                pass
            print(traceback.format_exc())
            print("=" * 80)
        return jsonify({"ok": False, "error": "当前AI识别服务异常，请稍后重试或手动添加食材"}), 500


@bp.post("/api/ai/parse-text")
def api_ai_parse_text():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "请输入要解析的文本"}), 400

    async_flag_in_json = bool(payload.get("async")) if "async" in payload else False
    async_flag_qs = str(request.args.get("async") or "").strip().lower() in {"1", "true", "yes", "on"}
    async_flag = async_flag_in_json or async_flag_qs
    try:
        if async_flag:
            job_id = ai_job_service.create_job(
                kind="ai_parse_text",
                fn=lambda: extract_items_from_text(text),
                meta={"text_len": len(text)},
                app=current_app._get_current_object(),
            )
            return jsonify({"ok": True, "async": True, "job_id": job_id})

        items = extract_items_from_text(text)
        return jsonify({"ok": True, "data": items})
    except RuntimeError as e:
        msg = str(e) or "AI 配置缺失"
        if "VOLCENGINE_API_KEY" in msg:
            return jsonify({"ok": False, "error": "AI 未配置：请设置环境变量 VOLCENGINE_API_KEY，或到「设置」页保存 VOLCENGINE_API_KEY。"}), 400
        return jsonify({"ok": False, "error": msg}), 400
    except Exception:
        verbose_raw = (os.environ.get("AI_LOG_VERBOSE") or "1").strip()
        verbose = verbose_raw in {"1", "true", "True", "YES", "yes", "on", "ON"}
        if verbose:
            print("=" * 80)
            print("[AI] /api/ai/parse-text failed")
            print(traceback.format_exc())
            print("=" * 80)
        return jsonify({"ok": False, "error": "当前AI识别服务异常，请稍后重试或手动添加食材"}), 500


@bp.post("/api/items/add-one")
def api_items_add_one():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "名称不能为空"}), 400
    quantity = safe_int(payload.get("quantity"), default=1)
    if quantity < 0:
        quantity = 0
    category = (payload.get("category") or "其他").strip() or "其他"
    location = (payload.get("location") or "冰箱").strip() or "冰箱"
    unit = (payload.get("unit") or "份").strip() or "份"
    note = (payload.get("note") or "").strip()
    shelf_life_days = payload.get("shelf_life_days")
    try:
        shelf_life_days_int = int(shelf_life_days) if shelf_life_days not in (None, "", "null") else None
    except Exception:
        shelf_life_days_int = None

    item = Item(
        name=name,
        category=category,
        quantity=float(quantity),
        unit=unit,
        location=location,
        note=note,
        shelf_life_days=shelf_life_days_int,
    )
    item.touch()
    db.session.add(item)
    db.session.commit()
    return jsonify({"ok": True, "item_id": item.id})

