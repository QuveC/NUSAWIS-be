import uuid
from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.rate_limit import enforce_rate_limit, get_client_ip
from app.core.security import decode_token
from app.core.token_store import is_token_revoked
from app.crud import crud_user
from app.models.user import UserRole
from app.schemas.user import UserOut

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/token")
# Sama seperti di atas tapi tidak error kalau header Authorization tidak ada (tamu).
oauth2_scheme_optional = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/token", auto_error=False
)

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Token tidak valid atau sudah kedaluwarsa",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_token_payload(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        payload = decode_token(token, "access")
    except jwt.PyJWTError:
        raise credentials_exception
    if is_token_revoked(payload):
        raise credentials_exception
    return payload


def get_current_user(
    payload: dict = Depends(get_token_payload), db: Session = Depends(get_db)
) -> UserOut:
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise credentials_exception

    user = crud_user.get_cached(db, user_id)
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akun tidak aktif")
    return user


def get_optional_user(
    token: str | None = Depends(oauth2_scheme_optional), db: Session = Depends(get_db)
) -> UserOut | None:
    """Untuk endpoint yang boleh diakses tamu.

    - Tanpa header Authorization -> None (tamu).
    - Ada token tapi tidak valid/kedaluwarsa -> 401, supaya frontend tahu harus refresh
      token (bukan diam-diam diperlakukan sebagai tamu).
    """
    if not token:
        return None
    return get_current_user(get_token_payload(token), db)


def rate_limit_identity(request: Request, user: UserOut | None) -> str:
    """User login dibatasi per akun (adil untuk yang satu jaringan/NAT), tamu per IP."""
    return f"user:{user.id}" if user else f"ip:{get_client_ip(request)}"


def home_rate_limit(
    request: Request, user: UserOut | None = Depends(get_optional_user)
) -> None:
    enforce_rate_limit(
        f"rl:home:{rate_limit_identity(request, user)}",
        settings.HOME_RATE_LIMIT_USER if user else settings.HOME_RATE_LIMIT_GUEST,
        settings.HOME_RATE_WINDOW_SECONDS,
        fail_open=True,
    )


def require_roles(*roles: UserRole) -> Callable[..., UserOut]:
    """Contoh: Depends(require_roles(UserRole.ADMIN))"""

    def checker(user: UserOut = Depends(get_current_user)) -> UserOut:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Akses ditolak"
            )
        return user

    return checker
