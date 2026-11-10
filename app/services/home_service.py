from sqlalchemy.orm import Session

from app.crud import crud_home
from app.schemas.home import HomeOut

FEATURED_LIMIT = 6


def get_home(db: Session) -> HomeOut:
    return HomeOut(
        banners=crud_home.list_banners(db, only_active=True),
        # tidak ada kolom is_featured: tampilkan destinasi PUBLISHED dengan rating tertinggi
        featured_destinations=crud_home.list_destinations(
            db, order_by_rating=True, limit=FEATURED_LIMIT
        ),
    )
