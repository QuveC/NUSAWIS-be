"""Cache JSON sederhana di Redis. Selalu fail-open: kalau Redis error, fallback ke DB."""

import json
import logging
import random
import time
from collections.abc import Callable
from typing import Any

import redis

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 10
_WAIT_STEP_SECONDS = 0.05
_WAIT_STEPS = 10  # maksimal ~0.5 detik menunggu worker lain mengisi cache


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


# ---------------- Namespace ber-versi (invalidasi O(1)) ----------------
#
# Key disusun sebagai "cache:{ns}:v{versi}:...". Untuk membuang SEMUA cache satu namespace
# cukup naikkan versinya (INCR) -- tidak perlu SCAN/DEL ribuan key. Key versi lama
# akan hilang sendiri karena TTL.


def _version_key(namespace: str) -> str:
    return f"cache:{namespace}:ver"


def namespaced_key(namespace: str, *parts: str) -> str:
    try:
        version = get_redis().get(_version_key(namespace)) or "0"
    except redis.RedisError:
        version = "0"
    return f"cache:{namespace}:v{version}:" + ":".join(parts)


def invalidate_namespace(namespace: str) -> None:
    try:
        get_redis().incr(_version_key(namespace))
    except redis.RedisError as exc:
        logger.warning("Invalidasi cache %s gagal: %s", namespace, exc)


# ---------------- Stale-while-revalidate + anti cache stampede ----------------


def _read_entry(client: redis.Redis, key: str) -> dict | None:
    try:
        raw = client.get(key)
    except redis.RedisError:
        return None
    if raw is None:
        return None
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict) or "exp" not in entry or "data" not in entry:
        return None
    return entry


def _write_entry(client: redis.Redis, key: str, data: Any, ttl: int, stale_grace: int) -> None:
    # Jitter +-10% supaya key-key yang dibuat bersamaan tidak kedaluwarsa bersamaan.
    fresh_until = time.time() + ttl * random.uniform(0.9, 1.1)
    try:
        client.set(key, json.dumps({"exp": fresh_until, "data": data}), ex=ttl + stale_grace)
    except redis.RedisError as exc:
        logger.warning("Cache SET gagal (%s): %s", key, exc)


def _try_lock(client: redis.Redis, key: str) -> bool:
    try:
        return bool(client.set(f"cache:lock:{key}", "1", nx=True, ex=_LOCK_TTL_SECONDS))
    except redis.RedisError:
        return True  # Redis bermasalah: biarkan request ini memuat dari DB


def _unlock(client: redis.Redis, key: str) -> None:
    try:
        client.delete(f"cache:lock:{key}")
    except redis.RedisError:
        pass


def cache_get_or_load(
    key: str,
    loader: Callable[[], Any],
    ttl: int,
    stale_grace: int | None = None,
) -> Any:
    """Ambil data dari cache; kalau tidak ada/kedaluwarsa, panggil `loader()` (query DB).

    Supaya server tidak melonjak saat cache habis dan banyak request datang bersamaan:
    - Hanya SATU request yang memegang lock dan menjalankan query ke DB.
    - Request lain menerima data lama (stale) selama masa `stale_grace`, atau menunggu
      sebentar (~0.5 detik) kalau cache benar-benar kosong.
    - Kalau Redis mati, langsung ke DB (fail-open).

    `loader` harus mengembalikan data yang bisa di-JSON-kan.
    """
    stale_grace = settings.CACHE_STALE_GRACE_SECONDS if stale_grace is None else stale_grace
    # Semua operasi Redis di bawah menelan error sendiri, jadi Redis mati = langsung loader().
    client = get_redis()
    entry = _read_entry(client, key)

    if entry is not None and entry["exp"] > time.time():
        return entry["data"]

    if _try_lock(client, key):
        try:
            data = loader()
            _write_entry(client, key, data, ttl, stale_grace)
            return data
        finally:
            _unlock(client, key)

    if entry is not None:
        return entry["data"]  # data basi, sementara worker lain memperbarui

    for _ in range(_WAIT_STEPS):
        time.sleep(_WAIT_STEP_SECONDS)
        entry = _read_entry(client, key)
        if entry is not None:
            return entry["data"]
    return loader()
