import uuid
from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import Double, and_, case, cast, func, literal, literal_column, or_, select, tuple_
from sqlalchemy.orm import Session, load_only
from sqlalchemy.sql.elements import ColumnElement

from app.core.cache import invalidate_namespace
from app.models.home import Banner, Category, DataStatus, Destination, District
from app.schemas.home import BannerCreate, DestinationCreate

# Semua cache halaman home/pencarian ada di namespace ini. Panggil
# invalidate_namespace(HOME_CACHE_NAMESPACE) setiap kali data destinasi/banner/kategori berubah.
HOME_CACHE_NAMESPACE = "home"

# HARUS sama dengan config di kolom generated destinations.search_vector di database.
SEARCH_TS_CONFIG = "indonesian"

SortOption = Literal["relevance", "name", "rating", "newest"]

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
    if db.get_bind().dialect.name == "postgresql":
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


def _filters(
    db: Session,
    *,
    tokens: Sequence[str],
    category_id: int | None,
    district_id: int | None,
) -> tuple[list[ColumnElement[bool]], ColumnElement[float] | None]:
    conditions = [_published()]  # publik hanya boleh melihat destinasi PUBLISHED
    if category_id is not None:
        conditions.append(Destination.category_id == category_id)
    if district_id is not None:
        conditions.append(Destination.district_id == district_id)
    rank = None
    if tokens:
        match, rank = _search_condition(db, tokens)
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
    *,
    tokens: Sequence[str] = (),
    category_id: int | None = None,
    district_id: int | None = None,
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
    conditions, rank = _filters(
        db, tokens=tokens, category_id=category_id, district_id=district_id
    )
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


def count_destinations(
    db: Session,
    *,
    tokens: Sequence[str] = (),
    category_id: int | None = None,
    district_id: int | None = None,
) -> int:
    conditions, _ = _filters(db, tokens=tokens, category_id=category_id, district_id=district_id)
    return db.scalar(select(func.count()).select_from(Destination).where(*conditions)) or 0


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
