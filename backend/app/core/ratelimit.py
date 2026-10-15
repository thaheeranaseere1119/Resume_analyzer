"""Fixed-window rate limiting backed by Redis, with an in-process fallback."""
from __future__ import annotations

import logging
import threading
import time

from fastapi import Request

from .config import get_settings
from .errors import AppError

log = logging.getLogger("app.ratelimit")
_mem: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()
_redis = None
_redis_failed_at = 0.0


def _client() :
    global _redis, _redis_failed_at
    if _redis is None and time.time() - _redis_failed_at > 30:
        try:
            import redis
            _redis = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)
            _redis.ping()
        except Exception:
            _redis, _redis_failed_at = None, time.time()
    return _redis


def hit(key: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    bucket = int(time.time() // window_s)
    k = f"rl:{key}:{bucket}"
    r = _client()
    if r is not None:
        try:
            n = r.incr(k)
            if n == 1:
                r.expire(k, window_s)
            return n <= limit, window_s - int(time.time() % window_s)
        except Exception:
            log.warning("redis rate limit unavailable; using in-process limiter")
    with _lock:
        count, exp = _mem.get(k, (0, time.time() + window_s))
        count += 1
        _mem[k] = (count, exp)
        if len(_mem) > 10000:
            now = time.time()
            for kk in [kk for kk, (_, e) in _mem.items() if e < now]:
                _mem.pop(kk, None)
    return count <= limit, window_s - int(time.time() % window_s)


def client_ip(request: Request) -> str:
    # Behind the bundled nginx the real address is in X-Real-IP; otherwise use the socket peer.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def limiter(scope: str, limit_attr: str, window_s: int):
    """FastAPI dependency factory: limit by client IP for the given scope."""
    def dep(request: Request):
        limit = getattr(get_settings(), limit_attr)
        ok, retry = hit(f"{scope}:{client_ip(request)}", limit, window_s)
        if not ok:
            raise AppError(429, "rate_limited", "Too many requests. Please wait and try again.", {"retry_after": retry})
    return dep
