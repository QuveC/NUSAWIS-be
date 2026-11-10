import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_token_payload
from app.core.config import settings
from app.core.rate_limit import (
    ensure_not_locked,
    login_rate_limiter,
    refresh_rate_limiter,
    register_failed_login,
    register_rate_limiter,
    reset_failed_login,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    fake_verify_password,
    verify_password,
)
from app.core.token_store import (
    blacklist_access_token,
    consume_refresh_token,
    is_token_revoked,
    revoke_all_user_tokens,
    revoke_refresh_token,
    store_refresh_token,
)
from app.crud import crud_user
from app.models.user import User
from app.schemas.user import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    Message,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["Auth"])

INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Email atau password salah",
    headers={"WWW-Authenticate": "Bearer"},
)
INVALID_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Refresh token tidak valid atau sudah kedaluwarsa",
)


def _issue_tokens(user_id: uuid.UUID, role) -> TokenPair:
    access_token, _ = create_access_token(user_id, getattr(role, "value", role))
    refresh_token, refresh_payload = create_refresh_token(user_id)
    store_refresh_token(refresh_payload)
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _authenticate(db: Session, email: str, password: str) -> User:
    email = email.strip().lower()
    ensure_not_locked(email)

    user = crud_user.get_by_email(db, email)
    if user is None:
        fake_verify_password()  # samakan waktu respons
        register_failed_login(email)
        raise INVALID_CREDENTIALS

    if not verify_password(password, user.password_hash):
        register_failed_login(email)
        raise INVALID_CREDENTIALS

    reset_failed_login(email)
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akun tidak aktif")
    return user


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limiter)],
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if crud_user.get_by_email(db, payload.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email sudah terdaftar")
    try:
        return crud_user.create(db, payload)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email sudah terdaftar")


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[Depends(login_rate_limiter)],
)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = _authenticate(db, payload.email, payload.password)
    tokens = _issue_tokens(user.id, user.role)
    return AuthResponse(**tokens.model_dump(), user=UserOut.model_validate(user))


@router.post(
    "/token",
    response_model=TokenPair,
    dependencies=[Depends(login_rate_limiter)],
    summary="Login form OAuth2 (untuk tombol Authorize di Swagger). Isi username = email.",
)
def login_form(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    if len(form_data.username) > 150 or len(form_data.password) > 128:
        raise INVALID_CREDENTIALS
    user = _authenticate(db, form_data.username, form_data.password)
    return _issue_tokens(user.id, user.role)


@router.post(
    "/refresh",
    response_model=TokenPair,
    dependencies=[Depends(refresh_rate_limiter)],
)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        claims = decode_token(payload.refresh_token, "refresh")
    except jwt.PyJWTError:
        raise INVALID_REFRESH

    # Rotasi: refresh token hanya bisa dipakai sekali.
    if not consume_refresh_token(claims):
        # Token sudah pernah dipakai -> kemungkinan dicuri. Cabut semua sesi user.
        revoke_all_user_tokens(claims["sub"])
        raise INVALID_REFRESH
    if is_token_revoked(claims):
        raise INVALID_REFRESH

    user = crud_user.get_cached(db, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise INVALID_REFRESH
    return _issue_tokens(user.id, user.role)


@router.post("/logout", response_model=Message)
def logout(body: LogoutRequest | None = None, access_claims: dict = Depends(get_token_payload)):
    blacklist_access_token(access_claims)
    if body and body.refresh_token:
        try:
            claims = decode_token(body.refresh_token, "refresh")
            if claims["sub"] == access_claims["sub"]:
                revoke_refresh_token(claims["jti"])
        except jwt.PyJWTError:
            pass
    return Message(message="Berhasil logout")


@router.post("/logout-all", response_model=Message)
def logout_all(access_claims: dict = Depends(get_token_payload)):
    revoke_all_user_tokens(access_claims["sub"])
    return Message(message="Semua sesi berhasil dicabut")
