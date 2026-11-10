import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.home import Banner, DataStatus, Destination
from app.schemas.home import BannerCreate, DestinationCreate


def list_banners(db: Session, only_active: bool = False) -> list[Banner]:
    stmt = select(Banner)
    if only_active:
        stmt = stmt.where(Banner.is_active.is_(True))
    return list(db.scalars(stmt.order_by(Banner.sort_order.asc(), Banner.id.asc())))


def get_banner(db: Session, banner_id: int) -> Banner | None:
    return db.get(Banner, banner_id)


def create_banner(db: Session, payload: BannerCreate) -> Banner:
    banner = Banner(**payload.model_dump())
    db.add(banner)
    db.commit()
    db.refresh(banner)
    return banner


def list_destinations(
    db: Session,
    *,
    only_published: bool = True,
    category_id: int | None = None,
    district_id: int | None = None,
    q: str | None = None,
    order_by_rating: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[Destination]:
    stmt = select(Destination)
    if only_published:
        stmt = stmt.where(Destination.status == DataStatus.PUBLISHED)
    if category_id is not None:
        stmt = stmt.where(Destination.category_id == category_id)
    if district_id is not None:
        stmt = stmt.where(Destination.district_id == district_id)
    if q:
        # escape wildcard LIKE agar input user diperlakukan sebagai teks biasa
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            or_(
                Destination.name.ilike(pattern, escape="\\"),
                Destination.address.ilike(pattern, escape="\\"),
            )
        )
    if order_by_rating:
        stmt = stmt.order_by(
            Destination.avg_rating.desc().nulls_last(),
            Destination.rating_count.desc().nulls_last(),
            Destination.name.asc(),
        )
    else:
        stmt = stmt.order_by(Destination.name.asc())
    return list(db.scalars(stmt.limit(limit).offset(offset)).unique())


def get_destination(
    db: Session, destination_id: uuid.UUID, only_published: bool = True
) -> Destination | None:
    destination = db.get(Destination, destination_id)
    if destination is None:
        return None
    if only_published and destination.status != DataStatus.PUBLISHED:
        return None
    return destination


def get_destination_by_slug(
    db: Session, slug: str, only_published: bool = True
) -> Destination | None:
    stmt = select(Destination).where(Destination.slug == slug)
    if only_published:
        stmt = stmt.where(Destination.status == DataStatus.PUBLISHED)
    return db.scalars(stmt).unique().first()


def create_destination(
    db: Session, payload: DestinationCreate, created_by: uuid.UUID | None = None
) -> Destination:
    destination = Destination(
        **payload.model_dump(), created_by=created_by, updated_by=created_by
    )
    db.add(destination)
    db.commit()
    db.refresh(destination)
    return destination
