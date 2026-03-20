from __future__ import annotations

from datetime import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import AuditLog, User
from ..utils.auth import (
    generate_strong_password,
    get_client_ip,
    get_or_create_csrf_token,
    login_user,
    logout_user,
    reset_login_failures,
    record_login_failure,
    check_login_locked,
    validate_password_strength,
)


bp = Blueprint("auth", __name__)


@bp.get("/setup")
def setup_get():
    # Only allow setup when no users exist.
    if User.query.count() > 0:
        return redirect(url_for("main.index"))
    return render_template("auth_setup.html")


@bp.post("/setup")
def setup_post():
    if User.query.count() > 0:
        return redirect(url_for("main.index"))
    username = (request.form.get("username") or "").strip()
    pwd = (request.form.get("password") or "").strip()
    pwd2 = (request.form.get("password2") or "").strip()
    if not username:
        flash("管理员账号不能为空。", "danger")
        return redirect(url_for("auth.setup_get"))
    if len(username) < 3:
        flash("管理员账号至少 3 个字符。", "danger")
        return redirect(url_for("auth.setup_get"))
    ok, msg = validate_password_strength(pwd, level="admin_owner")
    if not ok:
        flash(msg or "管理员密码强度不符合要求。", "danger")
        return redirect(url_for("auth.setup_get"))
    if pwd != pwd2:
        flash("两次输入的密码不一致。", "danger")
        return redirect(url_for("auth.setup_get"))

    if User.query.filter_by(username=username).first():
        flash("该用户名已存在。", "danger")
        return redirect(url_for("auth.setup_get"))

    user = User(
        username=username,
        password_hash=generate_password_hash(pwd),
        role="owner",
        created_at=datetime.utcnow(),
        must_change_password=False,
    )
    db.session.add(user)
    db.session.flush()

    ip = get_client_ip()
    db.session.add(
        AuditLog(
            actor_user_id=None,
            actor_username=None,
            action_type="setup_owner_created",
            target_user_id=user.id,
            target_username=user.username,
            ip=ip,
            user_agent=str(getattr(request, "user_agent", None) or ""),
            meta_json='{}',
        )
    )
    db.session.commit()
    login_user(user)
    flash("初始化完成：已创建超级管理员（Owner）。", "success")
    nxt = (request.args.get("next") or "").strip()
    if nxt.startswith("/"):
        return redirect(nxt)
    return redirect(url_for("main.index"))


@bp.get("/login")
def login():
    if getattr(g, "current_user", None) is not None:
        return redirect(url_for("main.index"))
    return render_template("auth_login.html")


@bp.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    pwd = (request.form.get("password") or "").strip()
    ip = get_client_ip()

    locked, retry_s = check_login_locked(username, ip)
    if locked:
        flash(f"由于多次登录失败，该账号/IP 已被临时锁定（{retry_s} 秒后重试）。", "danger")
        return redirect(url_for("auth.login", next=request.args.get("next") or ""))

    u = User.query.filter_by(username=username).first()
    if u is None:
        # Keep generic error to reduce username enumeration.
        record_login_failure(username, ip)
        flash("账号或密码错误。", "danger")
        return redirect(url_for("auth.login", next=request.args.get("next") or ""))

    if not bool(getattr(u, "active", True)):
        flash("该账号已被禁用，请联系管理员。", "danger")
        return redirect(url_for("auth.login", next=request.args.get("next") or ""))

    if not check_password_hash(u.password_hash, pwd):
        record_login_failure(username, ip)
        flash("账号或密码错误。", "danger")
        return redirect(url_for("auth.login", next=request.args.get("next") or ""))

    # Login success: reset lock state
    reset_login_failures(username, ip)
    u.last_login_at = datetime.utcnow()
    db.session.commit()

    login_user(u)
    if getattr(u, "must_change_password", False):
        flash("请先完成强制改密。", "warning")
        return redirect(url_for("auth.change_password_get"))

    flash("已登录。", "success")
    nxt = (request.args.get("next") or "").strip()
    if nxt.startswith("/"):
        return redirect(nxt)
    return redirect(url_for("main.index"))


@bp.post("/logout")
def logout():
    logout_user()
    # refresh CSRF token after logout to avoid token reuse confusion
    get_or_create_csrf_token()
    flash("已退出登录。", "success")
    return redirect(url_for("auth.login"))


@bp.get("/change-password")
def change_password_get():
    u: User | None = getattr(g, "current_user", None)
    if u is None:
        return redirect(url_for("auth.login", next=request.full_path if request.full_path else request.path))
    if not getattr(u, "must_change_password", False):
        return redirect(url_for("main.index"))
    return render_template("auth_change_password.html")


@bp.post("/change-password")
def change_password_post():
    u: User | None = getattr(g, "current_user", None)
    if u is None:
        return redirect(url_for("auth.login", next=request.full_path if request.full_path else request.path))
    if not getattr(u, "must_change_password", False):
        return redirect(url_for("main.index"))

    new_pwd = (request.form.get("new_password") or "").strip()
    new_pwd2 = (request.form.get("new_password2") or "").strip()
    if not new_pwd:
        flash("请输入新密码。", "danger")
        return redirect(url_for("auth.change_password_get"))
    if new_pwd != new_pwd2:
        flash("两次输入的密码不一致。", "danger")
        return redirect(url_for("auth.change_password_get"))

    level = "admin_owner" if getattr(u, "is_admin", None) and u.is_admin() else "user"
    ok, msg = validate_password_strength(new_pwd, level=level)
    if not ok:
        flash(msg or "密码强度不符合要求。", "danger")
        return redirect(url_for("auth.change_password_get"))

    u.password_hash = generate_password_hash(new_pwd)
    u.must_change_password = False
    db.session.commit()

    ip = get_client_ip()
    db.session.add(
        AuditLog(
            actor_user_id=u.id,
            actor_username=u.username,
            action_type="must_change_password_completed",
            target_user_id=u.id,
            target_username=u.username,
            ip=ip,
            user_agent=str(getattr(request, "user_agent", None) or ""),
            meta_json='{}',
        )
    )
    db.session.commit()
    flash("密码已更新。", "success")
    return redirect(url_for("main.index"))

