from __future__ import annotations

import re
import secrets
import string
import time
from functools import wraps
from typing import Callable, TypeVar, Literal

from flask import abort, g, redirect, request, session, url_for

from ..models import User

T = TypeVar("T")


SESSION_USER_ID_KEY = "user_id"
SESSION_CSRF_KEY = "csrf_token"

# Login brute-force lock (in-memory, per-process).
# Keyed by (username, ip). After N failures -> locked_until.
_LOGIN_LOCK: dict[str, dict[str, float | int]] = {}
_LOGIN_FAIL_LIMIT = 5
_LOGIN_LOCK_SECONDS = 15 * 60


def get_client_ip() -> str:
    """
    Best-effort: prefer X-Forwarded-For first hop (nginx), fallback to remote_addr.
    """
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return xff or (request.remote_addr or "unknown")


def _lock_key(username: str, ip: str) -> str:
    u = (username or "").strip().lower()
    return f"{u}:{ip}"


def check_login_locked(username: str, ip: str) -> tuple[bool, int]:
    """
    Returns (locked, retry_after_seconds).
    """
    key = _lock_key(username, ip)
    v = _LOGIN_LOCK.get(key)
    if not v:
        return False, 0
    locked_until = float(v.get("locked_until") or 0)
    if locked_until > time.time():
        retry = int(max(1, locked_until - time.time()))
        return True, retry
    return False, 0


def record_login_failure(username: str, ip: str) -> None:
    key = _lock_key(username, ip)
    now = time.time()
    v = _LOGIN_LOCK.get(key) or {"fail_count": 0, "locked_until": 0}
    fail_count = int(v.get("fail_count") or 0) + 1
    if fail_count >= _LOGIN_FAIL_LIMIT:
        _LOGIN_LOCK[key] = {"fail_count": fail_count, "locked_until": now + _LOGIN_LOCK_SECONDS}
    else:
        _LOGIN_LOCK[key] = {"fail_count": fail_count, "locked_until": 0}


def reset_login_failures(username: str, ip: str) -> None:
    key = _lock_key(username, ip)
    _LOGIN_LOCK.pop(key, None)


def validate_password_strength(password: str, *, level: Literal["user", "admin_owner"]) -> tuple[bool, str]:
    """
    Dual-side (front/back) policy:
    - user: length>=8, must contain letters+digits
    - admin_owner: length>=12, must contain letters+digits+special, and satisfy >=3 char classes
    """
    pwd = password or ""
    if len(pwd) < 8:
        return False, "密码至少 8 位。"

    has_letter = bool(re.search(r"[A-Za-z]", pwd))
    has_digit = bool(re.search(r"[0-9]", pwd))
    if not (has_letter and has_digit):
        return False, "密码必须同时包含字母和数字。"

    if level == "admin_owner":
        if len(pwd) < 12:
            return False, "管理员密码至少 12 位。"
        has_special = bool(re.search(r"[^A-Za-z0-9]", pwd))
        if not has_special:
            return False, "管理员密码必须包含至少一个特殊字符。"
        classes = {
            "lower": bool(re.search(r"[a-z]", pwd)),
            "upper": bool(re.search(r"[A-Z]", pwd)),
            "digit": bool(re.search(r"[0-9]", pwd)),
            "special": bool(re.search(r"[^A-Za-z0-9]", pwd)),
        }
        if sum(1 for _k, ok in classes.items() if ok) < 3:
            return False, "管理员密码强度不足（请组合多种字符类型）。"

    return True, ""


def generate_strong_password(*, length: int = 18) -> str:
    """
    Generate a password that passes admin_owner policy.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{};:,.?/~"
    # Keep trying until policy matches (usually succeeds quickly).
    for _ in range(50):
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        ok, _msg = validate_password_strength(pwd, level="admin_owner")
        if ok:
            return pwd
    # Fallback (should be extremely rare).
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
    session["last_activity_at"] = time.time()
    session.permanent = True


def logout_user() -> None:
    # Full server-side session clear (requirement: logout must invalidate session).
    session.clear()


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

