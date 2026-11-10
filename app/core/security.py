import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt

from app.core.config import settings

TokenType = Literal["access", "refresh"]

# Batas bcrypt adalah 72 byte; password lebih panjang ditolak di schema.
BCRYPT_MAX_BYTES = 72


# ---------------- Password ----------------


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    data = password.encode("utf-8")
    if len(data) > BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(data, password_hash.encode("utf-8"))
    except ValueError:
        return False


# Hash dummy agar waktu respons login sama saja, baik email terdaftar maupun tidak
# (mencegah user enumeration lewat timing).
_DUMMY_HASH = hash_password(uuid.uuid4().hex)


def fake_verify_password() -> None:
    bcrypt.checkpw(b"dummy-password", _DUMMY_HASH.encode("utf-8"))


# ---------------- JWT ----------------


def _create_token(
    user_id, token_type: TokenType, expires_delta: timedelta, extra: dict | None = None
) -> tuple[str, dict]:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        # timestamp float supaya pencabutan token (revoked_before) presisi
        "iat": now.timestamp(),
        "nbf": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, payload


def create_access_token(user_id, role: str) -> tuple[str, dict]:
    return _create_token(
        user_id,
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        {"role": role},
    )


def create_refresh_token(user_id) -> tuple[str, dict]:
    return _create_token(
        user_id, "refresh", timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )


def decode_token(token: str, expected_type: TokenType) -> dict:
    """Validasi signature, exp, iss, aud, dan tipe token. Raise jwt.PyJWTError jika tidak valid."""
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],  # algoritma dikunci, cegah alg=none
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
        options={"require": ["exp", "iat", "nbf", "sub", "jti", "type", "iss", "aud"]},
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Tipe token salah")
    return payload
