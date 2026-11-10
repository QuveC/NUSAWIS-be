import logging

import redis
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    return _client


def set_redis_client(client: redis.Redis | None) -> None:
    """Dipakai test untuk mengganti client (mis. fakeredis)."""
    global _client
    _client = client


def redis_unavailable(exc: Exception) -> None:
    """Dipanggil saat Redis error pada operasi yang berkaitan dengan keamanan.

    Jika REDIS_FAIL_OPEN=False (default) request ditolak dengan 503,
    supaya rate limiter / pencabutan token tidak bisa dilewati saat Redis mati.
    """
    logger.error("Redis tidak tersedia: %s", exc)
    if not settings.REDIS_FAIL_OPEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan sementara tidak tersedia, coba lagi nanti",
        )
