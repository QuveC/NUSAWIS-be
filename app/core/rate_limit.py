import hashlib
import math
import time
import uuid

import redis
from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.redis import get_redis, redis_unavailable


def get_client_ip(request: Request) -> str:
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(max(retry_after, 1))},
    )


def hit_sliding_window(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Catat satu hit. Return (diizinkan, retry_after_detik)."""
    now = time.time()
    window_start = now - window_seconds
    member = f"{now}:{uuid.uuid4().hex}"

    pipe = get_redis().pipeline(transaction=True)
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.zrange(key, 0, 0, withscores=True)
    pipe.expire(key, window_seconds)
    _, _, count, oldest, _ = pipe.execute()

    if count <= limit:
        return True, 0

    oldest_ts = oldest[0][1] if oldest else now
    retry_after = math.ceil(oldest_ts + window_seconds - now)
    return False, retry_after


class RateLimiter:
    """Dependency FastAPI: batasi jumlah request per IP untuk satu scope."""

    def __init__(self, scope: str, limit: int, window_seconds: int):
        self.scope = scope
        self.limit = limit
        self.window_seconds = window_seconds

    def __call__(self, request: Request) -> None:
        key = f"rl:{self.scope}:{get_client_ip(request)}"
        try:
            allowed, retry_after = hit_sliding_window(key, self.limit, self.window_seconds)
        except redis.RedisError as exc:
            redis_unavailable(exc)
            return
        if not allowed:
            raise _too_many(
                retry_after,
                f"Terlalu banyak request. Coba lagi dalam {retry_after} detik",
            )


# ---------------- Lockout per akun ----------------


def _lockout_key(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return f"login_fail:{digest}"


def ensure_not_locked(email: str) -> None:
    key = _lockout_key(email)
    try:
        r = get_redis()
        attempts = r.get(key)
        if attempts is not None and int(attempts) >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            ttl = r.ttl(key)
            raise _too_many(
                ttl,
                "Terlalu banyak percobaan login gagal. "
                f"Akun dikunci sementara, coba lagi dalam {max(ttl, 1)} detik",
            )
    except redis.RedisError as exc:
        redis_unavailable(exc)


def register_failed_login(email: str) -> None:
    key = _lockout_key(email)
    try:
        pipe = get_redis().pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, settings.LOGIN_LOCKOUT_SECONDS)
        pipe.execute()
    except redis.RedisError as exc:
        redis_unavailable(exc)


def reset_failed_login(email: str) -> None:
    try:
        get_redis().delete(_lockout_key(email))
    except redis.RedisError as exc:
        redis_unavailable(exc)


login_rate_limiter = RateLimiter(
    "login", settings.LOGIN_RATE_LIMIT, settings.LOGIN_RATE_WINDOW_SECONDS
)
register_rate_limiter = RateLimiter(
    "register", settings.REGISTER_RATE_LIMIT, settings.REGISTER_RATE_WINDOW_SECONDS
)
refresh_rate_limiter = RateLimiter(
    "refresh", settings.REFRESH_RATE_LIMIT, settings.REFRESH_RATE_WINDOW_SECONDS
)
