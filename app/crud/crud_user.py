import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache import cache_delete, cache_get, cache_set, user_cache_key
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.schemas.user import RegisterRequest, UserOut, UserUpdate


def get(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower()))


def get_cached(db: Session, user_id: uuid.UUID) -> UserOut | None:
    """Ambil data user dari Redis; kalau miss, ambil dari DB lalu simpan ke cache."""
    cached = cache_get(user_cache_key(user_id))
    if cached is not None:
        try:
            return UserOut.model_validate(cached)
        except ValueError:
            cache_delete(user_cache_key(user_id))

    user = get(db, user_id)
    if user is None:
        return None
    data = UserOut.model_validate(user)
    cache_set(user_cache_key(user_id), data.model_dump(mode="json"))
    return data


def invalidate_cache(user_id: uuid.UUID) -> None:
    cache_delete(user_cache_key(user_id))


def create(db: Session, payload: RegisterRequest) -> User:
    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=UserRole.USER,  # role tidak pernah diambil dari input publik
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update(db: Session, user: User, payload: UserUpdate) -> User:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    invalidate_cache(user.id)
    return user


def set_password(db: Session, user: User, new_password: str) -> User:
    user.password_hash = hash_password(new_password)
    db.commit()
    db.refresh(user)
    invalidate_cache(user.id)
    return user
