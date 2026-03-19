from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlparse

from flask import Blueprint, flash, jsonify, redirect, request, url_for

from ...extensions import db
from ...models import Item
from ...services.items_service import mark_used_up, use_up
from ...utils.auth import admin_required


bp = Blueprint("items", __name__)


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


@bp.post("/item/<int:item_id>/use-up")
def use_up_route(item_id: int):
    item = Item.query.get_or_404(item_id)
    item = use_up(item)
    flash(f"已标记用完：{item.name}", "warning")
    return redirect(request.referrer or url_for("main.index"))

