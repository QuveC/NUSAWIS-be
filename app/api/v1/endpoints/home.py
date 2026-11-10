import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import crud_home
from app.schemas.home import BannerOut, DestinationOut, HomeOut
from app.services import home_service

router = APIRouter(prefix="/home", tags=["Home"])

NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destinasi tidak ditemukan")


@router.get("", response_model=HomeOut)
def read_home(db: Session = Depends(get_db)):
    return home_service.get_home(db)


@router.get("/banners", response_model=list[BannerOut])
def list_banners(only_active: bool = True, db: Session = Depends(get_db)):
    return crud_home.list_banners(db, only_active=only_active)


@router.get("/destinations", response_model=list[DestinationOut])
def list_destinations(
    category_id: int | None = None,
    district_id: int | None = None,
    q: str | None = Query(default=None, max_length=100, description="Cari nama/alamat"),
    sort: str = Query(default="name", pattern="^(name|rating)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crud_home.list_destinations(
        db,
        category_id=category_id,
        district_id=district_id,
        q=q,
        order_by_rating=sort == "rating",
        limit=limit,
        offset=offset,
    )


@router.get("/destinations/slug/{slug}", response_model=DestinationOut)
def get_destination_by_slug(slug: str, db: Session = Depends(get_db)):
    destination = crud_home.get_destination_by_slug(db, slug)
    if destination is None:
        raise NOT_FOUND
    return destination


@router.get("/destinations/{destination_id}", response_model=DestinationOut)
def get_destination(destination_id: uuid.UUID, db: Session = Depends(get_db)):
    destination = crud_home.get_destination(db, destination_id)
    if destination is None:
        raise NOT_FOUND
    return destination
