from __future__ import annotations

import os
import traceback
from urllib.parse import parse_qs, urlparse

from flask import Blueprint, flash, jsonify, redirect, request, url_for

from ..extensions import db
from ..models import Item
from ..services.barcode_service import barcode_lookup
from ..services.items_service import adjust_quantity, mark_used_up, use_up
from ..services.settings_service import safe_int
from ..utils.ai_image import recognize_foods_from_image
from ..utils.ai_text import extract_items_from_text


bp = Blueprint("items", __name__)


@bp.post("/item/<int:item_id>/adjust")
def adjust(item_id: int):
    item = Item.query.get_or_404(item_id)
    try:
        delta = float((request.form.get("delta") or "0").strip())
    except ValueError:
        flash("调整数量失败：delta 不是数字。", "danger")
        return redirect(request.referrer or url_for("main.index"))

    item = adjust_quantity(item, delta=delta)
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
    if wants_json:
        return jsonify({"ok": True, "item_id": item.id, "quantity": item.quantity, "used_up": bool(item.used_up)})
    return redirect(request.referrer or url_for("main.index"))


@bp.post("/item/<int:item_id>/mark-used-up")
def mark_used_up_route(item_id: int):
    item = Item.query.get_or_404(item_id)
    item = mark_used_up(item)
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
    if wants_json:
        return jsonify({"ok": True, "item_id": item.id, "used_up": True})
    return redirect(request.referrer or url_for("main.index", view="usedup"))


@bp.post("/item/<int:item_id>/delete")
def delete(item_id: int):
    item = Item.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash(f"已删除：{item.name}", "warning")

    ref = request.referrer
    if ref:
        try:
            parsed = urlparse(ref)
            qs = parse_qs(parsed.query)
            view = (qs.get("view") or ["all"])[0] or "all"
            return redirect(url_for("main.index", view=view))
        except Exception:
            pass
    return redirect(url_for("main.index"))


@bp.post("/item/<int:item_id>/use-up")
def use_up_route(item_id: int):
    item = Item.query.get_or_404(item_id)
    item = use_up(item)
    flash(f"已标记用完：{item.name}", "warning")
    return redirect(request.referrer or url_for("main.index"))


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
                "quick_step": r.quick_step,
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

    existing = Item.query.filter(Item.barcode == barcode).order_by(Item.id.desc()).first()
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
    try:
        img = f.read()
        if not img:
            return jsonify({"ok": False, "error": "缺少图片"}), 400
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
    try:
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
    item = Item(
        name=name,
        category=category,
        quantity=float(quantity),
        unit=unit,
        location=location,
        note=note,
    )
    item.touch()
    db.session.add(item)
    db.session.commit()
    return jsonify({"ok": True, "item_id": item.id})


@bp.post("/api/items/batch-add")
def api_items_batch_add():
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return jsonify({"ok": False, "error": "items 必须是数组"}), 400

    created_ids: list[int] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        quantity = safe_int(it.get("quantity"), default=1)
        if quantity < 0:
            quantity = 0
        category = (it.get("category") or "其他").strip() or "其他"
        location = (it.get("location") or "冰箱").strip() or "冰箱"
        unit = (it.get("unit") or "份").strip() or "份"
        note = (it.get("note") or "").strip()
        obj = Item(
            name=name,
            category=category,
            quantity=float(quantity),
            unit=unit,
            location=location,
            note=note,
        )
        obj.touch()
        db.session.add(obj)
        db.session.flush()
        created_ids.append(obj.id)

    db.session.commit()
    return jsonify({"ok": True, "created": created_ids})


@bp.post("/api/items/<int:item_id>/delete-json")
def api_items_delete_json(item_id: int):
    item = Item.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({"ok": True, "item_id": item_id})

