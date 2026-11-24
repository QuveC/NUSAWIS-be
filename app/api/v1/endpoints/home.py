import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_user, home_rate_limit, rate_limit_identity
from app.core.config import settings
from app.core.rate_limit import enforce_rate_limit
from app.crud import crud_home
from app.crud.crud_home import SortOption
from app.schemas.home import (
    BannerOut,
    CategoryWithCount,
    DestinationOut,
    DestinationPage,
    FilterOptions,
    HomeOut,
)
from app.schemas.user import UserOut
from app.services import home_service

# Semua endpoint /home kena rate limit (tamu per IP, user per akun).
router = APIRouter(prefix="/home", tags=["Home"], dependencies=[Depends(home_rate_limit)])

NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destinasi tidak ditemukan")
MAX_PRICE = 2_147_483_647  # batas kolom INTEGER di database


def _set_cache_headers(response: Response, user: UserOut | None) -> None:
    """Izinkan browser/CDN menyimpan respons sebentar -> request berulang tidak sampai server.

    Respons tamu boleh di-cache bersama (public). Respons user login hanya di browser user
    itu sendiri (private). Vary: Authorization mencegah keduanya tertukar.
    """
    scope = "private" if user else "public"
    response.headers["Cache-Control"] = f"{scope}, max-age={settings.HOME_HTTP_MAX_AGE_SECONDS}"
    response.headers["Vary"] = "Authorization"


@router.get(
    "",
    response_model=HomeOut,
    summary="Halaman home: banner, kategori, kecamatan, unggulan & daftar wisata",
    description=(
        "Boleh diakses tanpa login. Tamu hanya mendapat "
        f"{settings.GUEST_PREVIEW_LIMIT} wisata pertama (`destinations.locked = true`). "
        "Kirim header `Authorization: Bearer <access_token>` untuk data penuh + `next_cursor`. "
        "Halaman berikutnya diambil lewat `GET /home/destinations?cursor=...`."
    ),
)
def read_home(
    response: Response,
    user: UserOut | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    _set_cache_headers(response, user)
    return home_service.get_home(db, user)


@router.get("/categories", response_model=list[CategoryWithCount])
def list_categories(response: Response, db: Session = Depends(get_db)):
    _set_cache_headers(response, None)  # sama untuk semua pengunjung
    return home_service.get_home_summary(db)["categories"]


@router.get("/banners", response_model=list[BannerOut])
def list_banners(response: Response, db: Session = Depends(get_db)):
    _set_cache_headers(response, None)  # hanya banner aktif
    return home_service.get_home_summary(db)["banners"]


@router.get(
    "/filters",
    response_model=FilterOptions,
    summary="Pilihan untuk panel filter: kategori, kecamatan, fasilitas, rentang harga",
)
def list_filter_options(response: Response, db: Session = Depends(get_db)):
    _set_cache_headers(response, None)  # sama untuk semua pengunjung
    return home_service.get_filter_options(db)


@router.get(
    "/destinations",
    response_model=DestinationPage,
    summary="Daftar, pencarian & filter wisata (cursor pagination)",
    description=(
        "Cari dengan `q`, filter `category_id`, `district_id`, `min_price`, `max_price` "
        "(0 = gratis), `min_rating`, `facilities`, `crowd_level`. "
        "Pilihan filter ada di `GET /home/filters`. "
        "Tamu: maksimal "
        f"{settings.GUEST_PREVIEW_LIMIT} hasil dan tidak bisa memakai `cursor` (401). "
        "User login: pakai `next_cursor` dari respons sebelumnya untuk halaman berikutnya, "
        "dengan `q`, filter, dan `sort` yang sama. "
        "Frontend sebaiknya memberi debounce ~300 ms pada kotak pencarian."
    ),
)
def list_destinations(
    request: Request,
    response: Response,
    q: str | None = Query(
        default=None, max_length=100, description="Kata kunci (minimal 2 karakter)"
    ),
    category_id: int | None = Query(default=None, ge=1, le=32767),
    district_id: int | None = Query(default=None, ge=1, le=32767),
    min_price: int | None = Query(
        default=None, ge=0, le=MAX_PRICE, description="Harga minimum (Rp)"
    ),
    max_price: int | None = Query(
        default=None, ge=0, le=MAX_PRICE, description="Harga maksimum (Rp). 0 = hanya yang gratis"
    ),
    min_rating: float | None = Query(default=None, ge=0, le=5, description="Rating minimum"),
    facilities: list[str] | None = Query(
        default=None,
        description=(
            "Wajib punya SEMUA fasilitas ini. Ulangi parameter "
            "(`?facilities=Parkir&facilities=Toilet`) atau pisahkan koma. "
            "Nilai diambil dari `GET /home/filters`."
        ),
    ),
    crowd_level: list[str] | None = Query(
        default=None,
        description=(
            "Tingkat keramaian: SEPI / SEDANG / RAMAI (boleh lebih dari satu: "
            "`?crowd_level=SEPI&crowd_level=SEDANG` atau `SEPI,SEDANG`). "
            f"Hanya data yang diperbarui dalam {settings.CROWD_STALE_HOURS:g} jam terakhir."
        ),
    ),
    sort: SortOption | None = Query(
        default=None,
        description="relevance (default jika ada q) | name (default) | rating | newest",
    ),
    limit: int = Query(
        default=settings.DESTINATION_PAGE_SIZE, ge=1, le=settings.DESTINATION_MAX_PAGE_SIZE
    ),
    cursor: str | None = Query(default=None, max_length=512),
    user: UserOut | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    query = home_service.build_query(
        q=q,
        category_id=category_id,
        district_id=district_id,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        facilities=facilities,
        crowd_levels=crowd_level,
        sort=sort,
        limit=limit,
        cursor=cursor,
    )
    if query.searched:
        # Pencarian lebih mahal dari browsing biasa -> batas tambahan yang lebih ketat.
        enforce_rate_limit(
            f"rl:search:{rate_limit_identity(request, user)}",
            settings.SEARCH_RATE_LIMIT_USER if user else settings.SEARCH_RATE_LIMIT_GUEST,
            settings.HOME_RATE_WINDOW_SECONDS,
            fail_open=True,
        )
    _set_cache_headers(response, user)
    return home_service.get_destination_page(db, query, user)


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
