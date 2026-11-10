"""Penyimpanan status token di Redis.

- refresh:{jti}            -> refresh token yang masih berlaku (allowlist, dipakai sekali / rotasi)
- blacklist:{jti}          -> access token yang sudah logout
- revoked_before:{user_id} -> semua token user yang diterbitkan sebelum waktu ini dianggap dicabut
"""

import time

import redis

from app.core.config import settings
from app.core.redis import get_redis, redis_unavailable


def _ttl_until(exp: int) -> int:
    return max(int(exp - time.time()), 1)


def store_refresh_token(payload: dict) -> None:
    try:
        get_redis().set(f"refresh:{payload['jti']}", payload["sub"], ex=_ttl_until(payload["exp"]))
    except redis.RedisError as exc:
        redis_unavailable(exc)


def consume_refresh_token(payload: dict) -> bool:
    """Hapus refresh token secara atomik. False jika sudah dipakai / dicabut."""
    try:
        owner = get_redis().getdel(f"refresh:{payload['jti']}")
    except redis.RedisError as exc:
        redis_unavailable(exc)
        return True
    return owner is not None and owner == payload["sub"]


def revoke_refresh_token(jti: str) -> None:
    try:
        get_redis().delete(f"refresh:{jti}")
    except redis.RedisError as exc:
        redis_unavailable(exc)


def blacklist_access_token(payload: dict) -> None:
    try:
        get_redis().set(f"blacklist:{payload['jti']}", "1", ex=_ttl_until(payload["exp"]))
    except redis.RedisError as exc:
        redis_unavailable(exc)


def revoke_all_user_tokens(user_id) -> None:
    ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    try:
        get_redis().set(f"revoked_before:{user_id}", repr(time.time()), ex=ttl)
    except redis.RedisError as exc:
        redis_unavailable(exc)


def is_token_revoked(payload: dict) -> bool:
    try:
        r = get_redis()
        pipe = r.pipeline(transaction=False)
        pipe.exists(f"blacklist:{payload['jti']}")
        pipe.get(f"revoked_before:{payload['sub']}")
        blacklisted, revoked_before = pipe.execute()
    except redis.RedisError as exc:
        redis_unavailable(exc)
        return False

    if blacklisted:
        return True
    if revoked_before is not None and float(payload["iat"]) < float(revoked_before):
        return True
    return False
