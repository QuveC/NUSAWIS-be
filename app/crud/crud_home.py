import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import (
    Double,
    Text,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    literal_column,
    or_,
    select,
    true,
    tuple_,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, load_only
from sqlalchemy.sql.elements import ColumnElement

from app.core.cache import invalidate_namespace
from app.core.config import settings
from app.models.home import (
    Banner,
    Category,
    CrowdLevel,
    CrowdStatus,
    DataStatus,
    Destination,
    District,
)
from app.schemas.home import BannerCreate, DestinationCreate

# Semua cache halaman home/pencarian ada di namespace ini. Panggil
# invalidate_namespace(HOME_CACHE_NAMESPACE) setiap kali data destinasi/banner/kategori berubah.
HOME_CACHE_NAMESPACE = "home"

# HARUS sama dengan config di kolom generated destinations.search_vector di database.
SEARCH_TS_CONFIG = "indonesian"

SortOption = Literal["relevance", "name", "rating", "newest"]


@dataclass(frozen=True)
class DestinationFilters:
    """Filter daftar destinasi. Semua nilai masuk ke SQL lewat bind parameter."""

    tokens: tuple[str, ...] = ()  # kata kunci yang sudah dinormalisasi
    category_id: int | None = None
    district_id: int | None = None
    min_price: int | None = None
    max_price: int | None = None  # 0 = hanya yang gratis
    min_rating: float | None = None
    facilities: tuple[str, ...] = ()  # wajib punya SEMUA fasilitas ini
    crowd_levels: tuple[str, ...] = ()  # salah satu dari level ini (SEPI/SEDANG/RAMAI)

# Hanya kolom yang dipakai DestinationCard yang di-SELECT (description, open_hours, dll. tidak).
_CARD_COLUMNS = (
    Destination.id,
    Destination.name,
    Destination.slug,
    Destination.address,
    Destination.avg_rating,
    Destination.rating_count,
    Destination.price_min,
    Destination.price_max,
)


def _published() -> ColumnElement[bool]:
    return Destination.status == DataStatus.PUBLISHED


def _rating() -> ColumnElement:
    return func.coalesce(Destination.avg_rating, 0)


def _rating_count() -> ColumnElement:
    return func.coalesce(Destination.rating_count, 0)


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


# ---------------- Banner ----------------


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
    invalidate_namespace(HOME_CACHE_NAMESPACE)
    return banner


# ---------------- Kategori & kecamatan ----------------


def list_categories_with_count(db: Session) -> list[tuple[Category, int]]:
    """Semua kategori + jumlah destinasi PUBLISHED di tiap kategori (satu query)."""
    stmt = (
        select(Category, func.count(Destination.id))
        .outerjoin(Destination, and_(Destination.category_id == Category.id, _published()))
        .group_by(Category.id)
        .order_by(Category.name.asc())
    )
    return [(category, count) for category, count in db.execute(stmt)]


def list_districts_with_count(db: Session) -> list[tuple[District, int]]:
    """Semua kecamatan + jumlah destinasi PUBLISHED di tiap kecamatan (satu query)."""
    stmt = (
        select(District, func.count(Destination.id))
        .outerjoin(Destination, and_(Destination.district_id == District.id, _published()))
        .group_by(District.id)
        .order_by(District.name.asc())
    )
    return [(district, count) for district, count in db.execute(stmt)]


# ---------------- Pencarian & daftar destinasi ----------------


def _search_condition(
    db: Session, tokens: Sequence[str]
) -> tuple[ColumnElement[bool], ColumnElement[float]]:
    """Kondisi WHERE + skor relevansi untuk kata kunci.

    `tokens` sudah dinormalisasi oleh service: huruf kecil, hanya huruf/angka
    (tidak ada karakter khusus tsquery / wildcard LIKE), jadi aman disusun langsung.
    """
    if _is_postgres(db):
        # Full-text search memakai kolom search_vector (GENERATED dari nama + deskripsi)
        # + GIN index. Config query sama dengan config kolom supaya stemming-nya cocok
        # ("berpasir" -> "pasir"). Tiap kata dicocokkan sebagai awalan supaya hasil sudah
        # muncul saat user masih mengetik: "pan bat" menemukan "Pantai Batu ...".
        # Semua kata wajib ada (&).
        tsquery = func.to_tsquery(
            literal_column(f"'{SEARCH_TS_CONFIG}'::regconfig"),
            " & ".join(f"{t}:*" for t in tokens),
        )
        match = Destination.search_vector.bool_op("@@")(tsquery)
        # cast ke double: ts_rank mengembalikan float4 yang tidak presisi saat bolak-balik
        # lewat cursor JSON (bisa membuat baris terakhir muncul lagi di halaman berikutnya).
        rank = cast(func.ts_rank(Destination.search_vector, tsquery), Double)
        return match, rank

    # Fallback non-Postgres (SQLite untuk test): ILIKE per kata pada kolom yang sama
    # dengan search_vector (nama + deskripsi), tanpa index.
    match = and_(
        *[
            or_(Destination.name.ilike(f"%{t}%"), Destination.description.ilike(f"%{t}%"))
            for t in tokens
        ]
    )
    rank = cast(case((Destination.name.ilike(f"{tokens[0]}%"), 1.0), else_=0.0), Double)
    return match, rank


def _facilities_condition(db: Session, facilities: Sequence[str]) -> ColumnElement[bool]:
    """Destinasi wajib punya SEMUA fasilitas yang diminta (cocok persis, case-sensitive).

    Nilai fasilitas sebaiknya diambil dari GET /home/filters supaya ejaannya sama dengan DB.
    """
    if _is_postgres(db):
        # facilities @> ARRAY[...] -> memakai GIN index ix_destinations_facilities
        return Destination.facilities.bool_op("@>")(
            literal(list(facilities), type_=ARRAY(Text))
        )
    # Fallback SQLite (test): kolom disimpan sebagai JSON array
    conditions = []
    for facility in facilities:
        items = func.json_each(Destination.facilities).table_valued("value")
        conditions.append(exists(select(1).select_from(items).where(items.c.value == facility)))
    return and_(*conditions)


def _crowd_condition(levels: Sequence[str]) -> ColumnElement[bool]:
    """Destinasi yang level keramaiannya salah satu dari `levels` DAN datanya masih baru.

    Data lebih lama dari CROWD_STALE_HOURS dianggap tidak diketahui, jadi tidak ikut.
    Memakai index ix_crowd_status_level (level, updated_at).
    """
    fresh_since = datetime.now(timezone.utc) - timedelta(hours=settings.CROWD_STALE_HOURS)
    return exists().where(
        CrowdStatus.destination_id == Destination.id,
        CrowdStatus.level.in_([CrowdLevel(level) for level in levels]),
        CrowdStatus.updated_at >= fresh_since,
    )


def _filters(
    db: Session, filters: DestinationFilters
) -> tuple[list[ColumnElement[bool]], ColumnElement[float] | None]:
    conditions = [_published()]  # publik hanya boleh melihat destinasi PUBLISHED
    if filters.category_id is not None:
        conditions.append(Destination.category_id == filters.category_id)
    if filters.district_id is not None:
        conditions.append(Destination.district_id == filters.district_id)

    # Harga: rentang harga destinasi [price_min, price_max] harus beririsan dengan rentang
    # filter. Destinasi yang harganya belum diisi (NULL) tidak ikut saat filter harga dipakai.
    if filters.max_price is not None:
        conditions.append(Destination.price_min <= filters.max_price)
    if filters.min_price is not None:
        conditions.append(
            func.coalesce(Destination.price_max, Destination.price_min) >= filters.min_price
        )

    if filters.min_rating is not None:
        conditions.append(_rating() >= filters.min_rating)
    if filters.facilities:
        conditions.append(_facilities_condition(db, filters.facilities))
    if filters.crowd_levels:
        conditions.append(_crowd_condition(filters.crowd_levels))

    rank = None
    if filters.tokens:
        match, rank = _search_condition(db, filters.tokens)
        conditions.append(match)
    return conditions, rank


def _sort_keys(
    sort: SortOption, rank: ColumnElement[float] | None
) -> tuple[list[ColumnElement], bool]:
    """Kolom urutan + arah (True = descending).

    `id` selalu jadi kunci terakhir sebagai pemutus seri, supaya urutan selalu unik dan
    cursor tidak pernah melewatkan / mengulang data.
    """
    if sort == "relevance" and rank is not None:
        return [rank, _rating(), Destination.id], True
    if sort in ("relevance", "rating"):
        return [_rating(), _rating_count(), Destination.id], True
    if sort == "newest":
        return [Destination.created_at, Destination.id], True
    return [Destination.name, Destination.id], False


def list_destination_cards(
    db: Session,
    filters: DestinationFilters = DestinationFilters(),
    *,
    sort: SortOption = "name",
    limit: int = 20,
    after: Sequence[Any] | None = None,
) -> list[tuple[Destination, list[Any]]]:
    """Daftar destinasi PUBLISHED (versi kartu) dengan keyset pagination.

    `after` = nilai kunci urutan baris terakhir halaman sebelumnya (isi cursor).
    Keyset pagination memakai `WHERE (kunci...) > (nilai_terakhir...)` alih-alih OFFSET,
    jadi halaman ke-100 sama ringannya dengan halaman pertama: DB tidak perlu membaca
    lalu membuang semua baris sebelumnya.

    Return: list (destinasi, nilai_kunci_urutan) -> nilai kunci dipakai untuk membuat cursor.
    """
    conditions, rank = _filters(db, filters)
    keys, descending = _sort_keys(sort, rank)

    if after is not None:
        if len(after) != len(keys):
            raise ValueError("Jumlah nilai cursor tidak sesuai")
        last = tuple_(*[literal(value, type_=key.type) for key, value in zip(keys, after)])
        conditions.append(tuple_(*keys) < last if descending else tuple_(*keys) > last)

    stmt = (
        select(Destination, *[key.label(f"sort_key_{i}") for i, key in enumerate(keys)])
        .options(load_only(*_CARD_COLUMNS))
        .where(*conditions)
        .order_by(*[key.desc() if descending else key.asc() for key in keys])
        .limit(limit)
    )
    return [(row[0], list(row[1:])) for row in db.execute(stmt)]


def count_destinations(db: Session, filters: DestinationFilters = DestinationFilters()) -> int:
    conditions, _ = _filters(db, filters)
    return db.scalar(select(func.count()).select_from(Destination).where(*conditions)) or 0


# ---------------- Pilihan filter ----------------


def list_facilities_with_count(db: Session) -> list[tuple[str, int]]:
    """Semua fasilitas yang dipakai destinasi PUBLISHED + jumlah destinasinya (satu query)."""
    if _is_postgres(db):
        # unnest(facilities) AS anon(value): pecah array jadi baris
        items = func.unnest(Destination.facilities).table_valued("value").render_derived()
    else:
        items = func.json_each(Destination.facilities).table_valued("value")
    count = func.count()
    stmt = (
        select(items.c.value, count)
        .select_from(Destination)
        .join(items, true())
        .where(_published())
        .group_by(items.c.value)
        .order_by(count.desc(), items.c.value.asc())
    )
    return [(name, n) for name, n in db.execute(stmt) if name]


def price_range(db: Session) -> tuple[int | None, int | None]:
    """Harga termurah & termahal di antara destinasi PUBLISHED (untuk slider harga)."""
    row = db.execute(
        select(
            func.min(Destination.price_min),
            func.max(func.coalesce(Destination.price_max, Destination.price_min)),
        ).where(_published())
    ).one()
    return row[0], row[1]


# ---------------- Detail destinasi ----------------


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
    invalidate_namespace(HOME_CACHE_NAMESPACE)
    return destination
