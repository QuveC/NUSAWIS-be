"""Cache JSON sederhana di Redis. Selalu fail-open: kalau Redis error, fallback ke DB."""

import json
import logging
from typing import Any

import redis

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


def user_cache_key(user_id) -> str:
    return f"cache:user:{user_id}"


def cache_get(key: str) -> Any | None:
    try:
        raw = get_redis().get(key)
    except redis.RedisError as exc:
        logger.warning("Cache GET gagal (%s): %s", key, exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    try:
        get_redis().set(key, json.dumps(value), ex=ttl or settings.USER_CACHE_TTL_SECONDS)
    except redis.RedisError as exc:
        logger.warning("Cache SET gagal (%s): %s", key, exc)


def cache_delete(*keys: str) -> None:
    if not keys:
        return
    try:
        get_redis().delete(*keys)
    except redis.RedisError as exc:
        logger.warning("Cache DELETE gagal (%s): %s", keys, exc)
