from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import credentials_exception, get_current_user, get_db
from app.core.token_store import revoke_all_user_tokens
from app.core.security import verify_password
from app.crud import crud_user
from app.schemas.user import ChangePasswordRequest, Message, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserOut)
def read_me(current_user: UserOut = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdate,
    current_user: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = crud_user.get(db, current_user.id)
    if user is None:
        raise credentials_exception
    return crud_user.update(db, user, payload)


@router.post("/me/password", response_model=Message)
def change_password(
    payload: ChangePasswordRequest,
    current_user: UserOut = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = crud_user.get(db, current_user.id)
    if user is None:
        raise credentials_exception
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password lama salah")
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password baru tidak boleh sama dengan password lama",
        )

    crud_user.set_password(db, user, payload.new_password)
    revoke_all_user_tokens(user.id)  # paksa login ulang di semua perangkat
    return Message(message="Password berhasil diubah, silakan login ulang")
