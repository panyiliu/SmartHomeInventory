from __future__ import annotations

from datetime import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import User
from ..utils.auth import get_or_create_csrf_token, login_user, logout_user


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
    if not pwd or len(pwd) < 8:
        flash("管理员密码至少 8 位。", "danger")
        return redirect(url_for("auth.setup_get"))
    if pwd != pwd2:
        flash("两次输入的密码不一致。", "danger")
        return redirect(url_for("auth.setup_get"))

    if User.query.filter_by(username=username).first():
        flash("该用户名已存在。", "danger")
        return redirect(url_for("auth.setup_get"))

    user = User(username=username, password_hash=generate_password_hash(pwd), role="admin", created_at=datetime.utcnow())
    db.session.add(user)
    db.session.commit()
    login_user(user)
    flash("初始化完成：已创建管理员账号。", "success")
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
    u = User.query.filter_by(username=username).first()
    if not u or not check_password_hash(u.password_hash, pwd):
        flash("账号或密码错误。", "danger")
        return redirect(url_for("auth.login", next=request.args.get("next") or ""))
    if not bool(getattr(u, "active", True)):
        flash("该账号已被禁用，请联系管理员。", "danger")
        return redirect(url_for("auth.login", next=request.args.get("next") or ""))
    u.last_login_at = datetime.utcnow()
    db.session.commit()
    login_user(u)
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

