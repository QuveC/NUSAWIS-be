import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import BCRYPT_MAX_BYTES
from app.models.user import UserRole

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _validate_name(value: str) -> str:
    value = " ".join(value.split())
    if len(value) < 2:
        raise ValueError("Nama minimal 2 karakter")
    if _CONTROL_CHARS.search(value) or re.search(r"[<>]", value):
        raise ValueError("Nama mengandung karakter yang tidak diizinkan")
    return value


def _validate_password_strength(value: str) -> str:
    errors = []
    if len(value) < 8:
        errors.append("minimal 8 karakter")
    if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
        errors.append(f"maksimal {BCRYPT_MAX_BYTES} byte")
    if not re.search(r"[a-z]", value):
        errors.append("huruf kecil")
    if not re.search(r"[A-Z]", value):
        errors.append("huruf besar")
    if not re.search(r"\d", value):
        errors.append("angka")
    if not re.search(r"[^A-Za-z0-9]", value):
        errors.append("simbol")
    if errors:
        raise ValueError("Password harus memenuhi: " + ", ".join(errors))
    return value


# ---------------- Request ----------------


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # tolak field asing seperti "role"

    name: str = Field(min_length=2, max_length=100)
    email: EmailStr = Field(max_length=150)
    password: str = Field(min_length=8, max_length=128)

    _norm_email = field_validator("email")(_normalize_email)
    _check_name = field_validator("name")(_validate_name)
    _check_password = field_validator("password")(_validate_password_strength)


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=150)
    password: str = Field(min_length=1, max_length=128)

    _norm_email = field_validator("email")(_normalize_email)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=2048)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=2048)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=100)

    _check_name = field_validator("name")(lambda v: _validate_name(v) if v is not None else v)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    _check_password = field_validator("new_password")(_validate_password_strength)


# ---------------- Response ----------------


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # detik, umur access token


class AuthResponse(TokenPair):
    user: UserOut


class Message(BaseModel):
    message: str
