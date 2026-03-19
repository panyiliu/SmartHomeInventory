from __future__ import annotations

import secrets
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from ..extensions import db
from ..models import User
from ..utils.auth import admin_required


bp = Blueprint("users", __name__, url_prefix="/admin/users")


@bp.get("")
@admin_required
def users_list():
    users = User.query.order_by(User.role.desc(), User.created_at.asc()).all()
    return render_template("admin_users.html", users=users)


@bp.post("/create")
@admin_required
def users_create():
    username = (request.form.get("username") or "").strip()
    role = (request.form.get("role") or "member").strip() or "member"
    active = (request.form.get("active") or "") == "1"
    pwd = (request.form.get("password") or "").strip()
    if not username:
        flash("用户名不能为空。", "danger")
        return redirect(url_for("users.users_list"))
    if len(username) < 3:
        flash("用户名至少 3 个字符。", "danger")
        return redirect(url_for("users.users_list"))
    if role not in {"admin", "member"}:
        role = "member"

    if User.query.filter_by(username=username).first():
        flash("该用户名已存在。", "danger")
        return redirect(url_for("users.users_list"))

    if not pwd:
        # Generate a safe password if admin leaves it blank.
        pwd = secrets.token_urlsafe(10)
        flash(f"已生成临时密码（请复制保存）：{pwd}", "warning")
    if len(pwd) < 8:
        flash("密码至少 8 位（或留空自动生成）。", "danger")
        return redirect(url_for("users.users_list"))

    u = User(
        username=username,
        password_hash=generate_password_hash(pwd),
        role=role,
        active=active,
        created_at=datetime.utcnow(),
    )
    db.session.add(u)
    db.session.commit()
    flash("成员已创建。", "success")
    return redirect(url_for("users.users_list"))


@bp.post("/<int:user_id>/toggle-active")
@admin_required
def users_toggle_active(user_id: int):
    u = User.query.get_or_404(user_id)
    # Prevent disabling the last admin
    if u.is_admin() and u.active:
        admins = User.query.filter_by(role="admin", active=True).count()
        if admins <= 1:
            flash("至少需要保留 1 个启用的管理员账号。", "danger")
            return redirect(url_for("users.users_list"))
    u.active = not bool(u.active)
    db.session.commit()
    flash("账号状态已更新。", "success")
    return redirect(url_for("users.users_list"))


@bp.post("/<int:user_id>/reset-password")
@admin_required
def users_reset_password(user_id: int):
    u = User.query.get_or_404(user_id)
    pwd = (request.form.get("new_password") or "").strip()
    if not pwd:
        pwd = secrets.token_urlsafe(10)
        flash(f"已生成新密码（请复制保存）：{pwd}", "warning")
    if len(pwd) < 8:
        flash("新密码至少 8 位（或留空自动生成）。", "danger")
        return redirect(url_for("users.users_list"))
    u.password_hash = generate_password_hash(pwd)
    db.session.commit()
    flash("密码已重置。", "success")
    return redirect(url_for("users.users_list"))

