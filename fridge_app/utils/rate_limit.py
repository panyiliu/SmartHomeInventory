from __future__ import annotations

import time
from dataclasses import dataclass

from flask import request


@dataclass
class _Bucket:
    reset_at: float
    count: int


_BUCKETS: dict[str, _Bucket] = {}


def _now() -> float:
    return time.time()


def _client_key() -> str:
    # Best-effort: prefer X-Forwarded-For first hop (nginx), fallback to remote_addr.
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = xff or (request.remote_addr or "unknown")
    return ip


def check_rate_limit(*, scope: str, limit: int, window_s: int) -> tuple[bool, int]:
    """
    Simple in-memory fixed-window limiter.
    Returns (allowed, retry_after_seconds).
    Note: per-process only (good enough for small self-host setups).
    """
    if limit <= 0 or window_s <= 0:
        return True, 0
    key = f"{scope}:{_client_key()}"
    now = _now()
    b = _BUCKETS.get(key)
    if b is None or now >= b.reset_at:
        _BUCKETS[key] = _Bucket(reset_at=now + float(window_s), count=1)
        return True, 0
    if b.count >= limit:
        retry = int(max(1, b.reset_at - now))
        return False, retry
    b.count += 1
    return True, 0

