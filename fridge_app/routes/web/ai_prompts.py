from __future__ import annotations

import json

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ...extensions import db
from ...models import AiPromptTemplate
from ...utils.auth import admin_required


bp = Blueprint("ai_prompts", __name__, url_prefix="/admin")


@bp.get("/ai-prompts")
@admin_required
def ai_prompts_list():
    categories = (
        AiPromptTemplate.query.with_entities(AiPromptTemplate.category_code)
        .distinct()
        .order_by(AiPromptTemplate.category_code.asc())
        .all()
    )
    category_codes = [c[0] for c in categories if c and c[0]]

    grouped: dict[str, list[AiPromptTemplate]] = {}
    for code in category_codes:
        rows = (
            AiPromptTemplate.query.filter(AiPromptTemplate.category_code == code)
            .order_by(AiPromptTemplate.is_default.desc(), AiPromptTemplate.updated_at.desc())
            .all()
        )
        grouped[code] = rows

    return render_template("ai_prompts.html", grouped=grouped, category_codes=category_codes)


@bp.route("/ai-prompts/new", methods=["GET", "POST"])
@admin_required
def ai_prompts_new():
    if request.method == "GET":
        return render_template(
            "ai_prompt_edit.html",
            prompt=None,
            is_new=True,
            preset_categories=["vision_recognize", "text_extract", "interface_test"],
        )

    category_code = (request.form.get("category_code") or "").strip()
    name = (request.form.get("name") or "").strip()
    content = request.form.get("content") or ""
    is_default = (request.form.get("is_default") or "") == "on"

    if not category_code or not name:
        flash("category_code 和 name 均不能为空。", "danger")
        return redirect(url_for("ai_prompts.ai_prompts_new"))

    row = AiPromptTemplate(category_code=category_code, name=name, content=content, is_default=False)
    if is_default:
        AiPromptTemplate.query.filter(
            AiPromptTemplate.category_code == category_code, AiPromptTemplate.is_default == True  # noqa
        ).update({"is_default": False})  # type: ignore
        row.is_default = True

    db.session.add(row)
    db.session.commit()
    flash("提示词已创建。", "success")
    return redirect(url_for("ai_prompts.ai_prompts_list"))


@bp.route("/ai-prompts/<int:prompt_id>/edit", methods=["GET", "POST"])
@admin_required
def ai_prompts_edit(prompt_id: int):
    row = AiPromptTemplate.query.get_or_404(prompt_id)
    if request.method == "GET":
        return render_template(
            "ai_prompt_edit.html",
            prompt=row,
            is_new=False,
            preset_categories=["vision_recognize", "text_extract", "interface_test"],
        )

    category_code = (request.form.get("category_code") or "").strip()
    name = (request.form.get("name") or "").strip()
    content = request.form.get("content") or ""
    is_default = (request.form.get("is_default") or "") == "on"

    if not category_code or not name:
        flash("category_code 和 name 均不能为空。", "danger")
        return redirect(url_for("ai_prompts.ai_prompts_edit", prompt_id=prompt_id))

    if is_default:
        AiPromptTemplate.query.filter(
            AiPromptTemplate.category_code == category_code, AiPromptTemplate.is_default == True  # noqa
        ).update({"is_default": False})  # type: ignore
        row.is_default = True
    else:
        row.is_default = False

    row.category_code = category_code
    row.name = name
    row.content = content
    db.session.commit()
    flash("提示词已保存。", "success")
    return redirect(url_for("ai_prompts.ai_prompts_list"))


@bp.post("/ai-prompts/<int:prompt_id>/delete")
@admin_required
def ai_prompts_delete(prompt_id: int):
    row = AiPromptTemplate.query.get_or_404(prompt_id)
    db.session.delete(row)
    db.session.commit()
    flash("提示词已删除。", "success")
    return redirect(url_for("ai_prompts.ai_prompts_list"))

