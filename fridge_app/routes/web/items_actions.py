from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlparse

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for

from ...extensions import db
from ...models import Item
from ...services.items_service import mark_used_up, use_up
from ...utils.auth import admin_required


bp = Blueprint("items", __name__)


@bp.post("/items/batch")
def batch_action():
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower().replace("-", "_")
    raw_ids = payload.get("ids") or []
    if action not in {"useup", "delete", "hard_delete"}:
        return jsonify({"ok": False, "error": "不支持的批量动作"}), 400
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"ok": False, "error": "ids 不能为空"}), 400

    ids: list[int] = []
    for x in raw_ids:
        try:
            v = int(str(x).strip())
            if v > 0 and v not in ids:
                ids.append(v)
        except Exception:
            continue
    if not ids:
        return jsonify({"ok": False, "error": "ids 不合法"}), 400

    # Guardrail to avoid accidental huge payload abuse.
    if len(ids) > 500:
        return jsonify({"ok": False, "error": "单次批量最多 500 条"}), 400

    now = datetime.utcnow()

    # Hard delete: permanent deletion of trash items.
    if action == "hard_delete":
        u = getattr(g, "current_user", None)
        if not u or not u.is_admin():
            abort(403)

        q = Item.query.filter(Item.id.in_(ids), Item.deleted_at.is_not(None))
        deleted_count = q.delete(synchronize_session=False)
        db.session.commit()
        return jsonify({"ok": True, "action": action, "requested": len(ids), "updated": int(deleted_count or 0)})

    q = Item.query.filter(Item.id.in_(ids), Item.deleted_at.is_(None))
    if action == "useup":
        updated = q.update(
            {
                Item.quantity: 0.0,
                Item.used_up: True,
                Item.updated_at: now,
            },
            synchronize_session=False,
        )
    else:  # delete (soft delete)
        updated = q.update(
            {
                Item.deleted_at: now,
                Item.updated_at: now,
            },
            synchronize_session=False,
        )
    db.session.commit()
    return jsonify({"ok": True, "action": action, "requested": len(ids), "updated": int(updated or 0)})


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
    item.deleted_at = datetime.utcnow()
    item.touch()
    db.session.commit()
    flash(f"已移入回收站：{item.name}", "warning")

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


@bp.post("/item/<int:item_id>/restore")
@admin_required
def restore(item_id: int):
    item = Item.query.get_or_404(item_id)
    item.deleted_at = None
    item.touch()
    db.session.commit()
    flash(f"已恢复：{item.name}", "success")
    return redirect(request.referrer or url_for("main.index", view="trash"))


@bp.post("/item/<int:item_id>/hard-delete")
@admin_required
def hard_delete(item_id: int):
    item = Item.query.get_or_404(item_id)
    if item.deleted_at is None:
        abort(400)

    item_name = item.name
    db.session.delete(item)
    db.session.commit()
    flash(f"已永久删除：{item_name}", "warning")
    return redirect(request.referrer or url_for("main.index", view="trash"))


@bp.post("/items/trash/hard-clear")
@admin_required
def trash_hard_clear():
    """
    Permanent delete all items in recycle bin (trash).
    """
    q = Item.query.filter(Item.deleted_at.is_not(None))
    deleted_count = q.delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True, "deleted": int(deleted_count or 0)})


@bp.post("/item/<int:item_id>/use-up")
def use_up_route(item_id: int):
    item = Item.query.get_or_404(item_id)
    item = use_up(item)
    flash(f"已标记用完：{item.name}", "warning")
    return redirect(request.referrer or url_for("main.index"))

