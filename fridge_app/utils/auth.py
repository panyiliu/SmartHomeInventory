from __future__ import annotations

import secrets
from functools import wraps
from typing import Callable, TypeVar

from flask import abort, g, redirect, request, session, url_for

from ..models import User

T = TypeVar("T")


SESSION_USER_ID_KEY = "user_id"
SESSION_CSRF_KEY = "csrf_token"


def get_or_create_csrf_token() -> str:
    tok = (session.get(SESSION_CSRF_KEY) or "").strip()
    if tok:
        return tok
    tok = secrets.token_urlsafe(32)
    session[SESSION_CSRF_KEY] = tok
    return tok


def verify_csrf() -> bool:
    expected = (session.get(SESSION_CSRF_KEY) or "").strip()
    if not expected:
        expected = get_or_create_csrf_token()
    supplied = (request.headers.get("X-CSRFToken") or "").strip()
    if not supplied:
        supplied = (request.form.get("csrf_token") or "").strip()
    return bool(supplied) and secrets.compare_digest(expected, supplied)


def load_current_user() -> User | None:
    uid = session.get(SESSION_USER_ID_KEY)
    try:
        if uid is None:
            return None
        return User.query.get(int(uid))
    except Exception:
        return None


def login_user(user: User) -> None:
    session[SESSION_USER_ID_KEY] = int(user.id)


def logout_user() -> None:
    session.pop(SESSION_USER_ID_KEY, None)


def login_required(fn: Callable[..., T]) -> Callable[..., T]:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if getattr(g, "current_user", None) is None:
            return redirect(url_for("auth.login", next=request.full_path if request.full_path else request.path))
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn: Callable[..., T]) -> Callable[..., T]:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u: User | None = getattr(g, "current_user", None)
        if u is None:
            return redirect(url_for("auth.login", next=request.full_path if request.full_path else request.path))
        if not u.is_admin():
            abort(403)
        return fn(*args, **kwargs)

    return wrapper

