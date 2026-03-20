from __future__ import annotations

from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import AuditLog, User
from ..utils.auth import admin_required
from ..utils.auth import generate_strong_password, get_client_ip, validate_password_strength


bp = Blueprint("users", __name__, url_prefix="/admin/users")


def _role_level(role: str) -> str:
    r = (role or "").lower()
    if r in {"owner", "admin"}:
        return "admin_owner"
    return "user"


def _audit(actor: User | None, *, action_type: str, target: User | None, meta: dict | None = None) -> None:
    meta = meta or {}
    db.session.add(
        AuditLog(
            actor_user_id=getattr(actor, "id", None),
            actor_username=getattr(actor, "username", None),
            action_type=action_type,
            target_user_id=getattr(target, "id", None),
            target_username=getattr(target, "username", None),
            ip=get_client_ip(),
            user_agent=str(getattr(request, "user_agent", None) or ""),
            meta_json=__import__("json").dumps(meta, ensure_ascii=False),
        )
    )


@bp.get("")
@admin_required
def users_list():
    users = User.query.order_by(User.role.desc(), User.created_at.asc()).all()
    return render_template("admin_users.html", users=users)


@bp.post("/create")
@admin_required
def users_create():
    from flask import g as _g

    actor: User = _g.current_user

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

    allowed_roles = {"owner", "admin", "member", "readonly"}
    if role not in allowed_roles:
        role = "member"
    if role == "owner" and not actor.is_owner():
        flash("只有 Owner 才能创建 Owner 账号。", "danger")
        return redirect(url_for("users.users_list"))

    if User.query.filter_by(username=username).first():
        flash("该用户名已存在。", "danger")
        return redirect(url_for("users.users_list"))

    level = _role_level(role)
    if not pwd:
        pwd = generate_strong_password(length=18)
        flash(f"已生成强密码（请复制保存）：{pwd}", "warning")
    else:
        ok, msg = validate_password_strength(pwd, level=level)
        if not ok:
            flash(msg or "密码强度不符合要求。", "danger")
            return redirect(url_for("users.users_list"))

    u = User(
        username=username,
        password_hash=generate_password_hash(pwd),
        role=role,
        active=active,
        created_at=datetime.utcnow(),
        must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    _audit(actor, action_type="user_created", target=u, meta={"role": role, "active": bool(active)})
    db.session.commit()
    flash("成员已创建。", "success")
    return redirect(url_for("users.users_list"))


@bp.post("/<int:user_id>/toggle-active")
@admin_required
def users_toggle_active(user_id: int):
    u = User.query.get_or_404(user_id)
    from flask import g as _g

    actor: User = _g.current_user

    confirm = (request.form.get("confirm_admin_password") or "").strip()
    if not confirm or not check_password_hash(actor.password_hash, confirm):
        flash("管理员密码错误，无法执行操作。", "danger")
        return redirect(url_for("users.users_list"))

    # Owner cannot be disabled.
    if u.is_owner():
        flash("Owner 账号不可禁用。", "danger")
        return redirect(url_for("users.users_list"))

    active_privileged = User.query.filter(User.active.is_(True), User.role.in_(["admin", "owner"])).count()
    # last admin protection: disabling active admin/admin-owner to 0 is not allowed.
    if u.active and u.role and u.role.lower() == "admin" and active_privileged <= 1:
        flash("至少需要保留 1 个启用的管理员（Owner/Admin）。", "danger")
        return redirect(url_for("users.users_list"))

    prev = bool(u.active)
    u.active = not bool(u.active)
    db.session.flush()
    _audit(actor, action_type="user_toggle_active", target=u, meta={"from_active": prev, "to_active": bool(u.active)})
    db.session.commit()
    flash("账号状态已更新。", "success")
    return redirect(url_for("users.users_list"))


@bp.post("/<int:user_id>/reset-password")
@admin_required
def users_reset_password(user_id: int):
    u = User.query.get_or_404(user_id)

    from flask import g as _g

    actor: User = _g.current_user
    confirm = (request.form.get("confirm_admin_password") or "").strip()
    if not confirm or not check_password_hash(actor.password_hash, confirm):
        flash("管理员密码错误，无法执行操作。", "danger")
        return redirect(url_for("users.users_list"))

    # Always generate strong random password; ignore any manual new_password input.
    pwd = generate_strong_password(length=20)
    u.password_hash = generate_password_hash(pwd)
    u.must_change_password = True
    db.session.flush()
    _audit(actor, action_type="user_reset_password", target=u, meta={"generated_length": len(pwd)})
    db.session.commit()

    flash("密码已重置。", "success")
    flash(f"新强密码（请复制保存）：{pwd}", "warning")
    return redirect(url_for("users.users_list"))

