"""Logika halaman home & pencarian destinasi.

Strategi supaya server tidak melonjak saat ramai:
1. Cache Redis untuk semua data publik (ringkasan home + tiap halaman daftar/pencarian),
   dengan stale-while-revalidate + lock -> saat cache habis hanya 1 request yang query DB.
2. Keyset (cursor) pagination, bukan OFFSET -> halaman jauh tetap ringan.
3. Payload kartu ringkas + batas ukuran halaman.
4. Kata kunci dinormalisasi (huruf kecil, spasi dirapikan, maks. 5 kata) supaya
   "Pantai  Indah" dan "pantai indah" memakai entri cache yang sama.
5. Tamu & user berbagi entri cache halaman pertama; tamu hanya menerima potongannya.
6. Full-text search Postgres + GIN index (lihat crud_home) dan rate limit di endpoint.
"""

import base64
import hashlib
import json
import math
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.cache import cache_get_or_load, namespaced_key
from app.core.config import settings
from app.crud import crud_home
from app.crud.crud_home import HOME_CACHE_NAMESPACE, SortOption
from app.models.home import Destination
from app.schemas.home import BannerOut, DestinationCard
from app.schemas.user import UserOut

MIN_QUERY_LENGTH = 2
MAX_QUERY_TOKENS = 5
MAX_TOKEN_LENGTH = 30
_TOKEN_RE = re.compile(r"[^\W_]+")  # huruf/angka (termasuk unicode), tanpa simbol

LOGIN_REQUIRED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Login untuk melihat lebih banyak wisata",
    headers={"WWW-Authenticate": "Bearer"},
)
INVALID_CURSOR = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST, detail="Cursor tidak valid"
)


@dataclass(frozen=True)
class DestinationQuery:
    tokens: tuple[str, ...] = ()
    searched: bool = False  # True kalau client mengirim q, meski tidak ada kata yang valid
    category_id: int | None = None
    district_id: int | None = None
    sort: SortOption = "name"
    limit: int = settings.DESTINATION_PAGE_SIZE
    cursor: str | None = None


# ---------------- Normalisasi kata kunci ----------------


def parse_search_query(q: str | None) -> tuple[bool, tuple[str, ...]]:
    """Return (ada_pencarian, tokens). Token: huruf kecil, hanya huruf/angka, unik."""
    if q is None:
        return False, ()
    normalized = " ".join(q.split())
    if not normalized:
        return False, ()
    if len(normalized) < MIN_QUERY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Kata kunci minimal {MIN_QUERY_LENGTH} karakter",
        )
    words = (w[:MAX_TOKEN_LENGTH] for w in _TOKEN_RE.findall(normalized.lower()))
    tokens = tuple(dict.fromkeys(words))[:MAX_QUERY_TOKENS]
    return True, tokens


def build_query(
    *,
    q: str | None = None,
    category_id: int | None = None,
    district_id: int | None = None,
    sort: SortOption | None = None,
    limit: int = settings.DESTINATION_PAGE_SIZE,
    cursor: str | None = None,
) -> DestinationQuery:
    searched, tokens = parse_search_query(q)
    effective_sort: SortOption = sort or ("relevance" if tokens else "name")
    if effective_sort == "relevance" and not tokens:
        effective_sort = "rating"  # tanpa kata kunci, "paling relevan" = rating tertinggi
    return DestinationQuery(
        tokens=tokens,
        searched=searched,
        category_id=category_id,
        district_id=district_id,
        sort=effective_sort,
        limit=limit,
        cursor=cursor or None,
    )


# ---------------- Cursor ----------------
#
# Cursor = base64url(JSON {"s": sort, "v": [nilai kunci urutan baris terakhir]}).
# Tidak perlu ditandatangani: memalsukan cursor hanya mengubah titik mulai halaman,
# dan tamu sama sekali tidak boleh memakai cursor.


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _finite_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("bukan angka")
    return number


def _finite_decimal(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("bukan angka")
    return number


def _short_str(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("teks tidak valid")
    return value


_CURSOR_PARSERS: dict[str, tuple[Callable[[Any], Any], ...]] = {
    "name": (_short_str, uuid.UUID),
    "rating": (_finite_decimal, int, uuid.UUID),
    "newest": (datetime.fromisoformat, uuid.UUID),
    "relevance": (_finite_float, _finite_decimal, uuid.UUID),
}


def encode_cursor(sort: SortOption, values: Sequence[Any]) -> str:
    payload = json.dumps({"s": sort, "v": [_to_jsonable(v) for v in values]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, sort: SortOption) -> list[Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        parsers = _CURSOR_PARSERS[sort]
        if data["s"] != sort or len(data["v"]) != len(parsers):
            raise ValueError("cursor tidak cocok dengan urutan")
        return [parse(value) for parse, value in zip(parsers, data["v"])]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise INVALID_CURSOR


# ---------------- Daftar & pencarian destinasi ----------------


def _card(destination: Destination) -> dict:
    return DestinationCard.model_validate(destination).model_dump(mode="json")


def _fingerprint(query: DestinationQuery) -> str:
    raw = json.dumps(
        [query.tokens, query.category_id, query.district_id, query.sort, query.limit, query.cursor],
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _empty_page(limit: int) -> dict:
    return {"items": [], "total": 0, "limit": limit, "has_more": False, "next_cursor": None}


def _load_page(db: Session, query: DestinationQuery, after: list[Any] | None) -> dict:
    filters = {
        "tokens": query.tokens,
        "category_id": query.category_id,
        "district_id": query.district_id,
    }
    # Ambil limit+1 baris: baris ekstra hanya penanda "masih ada halaman berikutnya".
    rows = crud_home.list_destination_cards(
        db, **filters, sort=query.sort, limit=query.limit + 1, after=after
    )
    has_more = len(rows) > query.limit
    rows = rows[: query.limit]

    total = None
    if query.cursor is None:
        # COUNT hanya di halaman pertama, dan dilewati kalau semua hasil sudah muat 1 halaman.
        total = crud_home.count_destinations(db, **filters) if has_more else len(rows)

    return {
        "items": [_card(destination) for destination, _ in rows],
        "total": total,
        "limit": query.limit,
        "has_more": has_more,
        "next_cursor": encode_cursor(query.sort, rows[-1][1]) if has_more else None,
    }


def _guest_preview(page: dict) -> dict:
    limit = settings.GUEST_PREVIEW_LIMIT
    locked = page["has_more"] or len(page["items"]) > limit
    return {
        **page,
        "items": page["items"][:limit],
        "limit": limit,
        "has_more": False,
        "next_cursor": None,
        "locked": locked,
        "message": f"Login untuk melihat semua {page['total']} wisata" if locked else None,
    }


def get_destination_page(db: Session, query: DestinationQuery, user: UserOut | None) -> dict:
    """Satu halaman daftar/pencarian destinasi.

    - User login : halaman penuh + next_cursor untuk halaman berikutnya.
    - Tamu       : hanya GUEST_PREVIEW_LIMIT item pertama, tanpa cursor, `locked=True`
                   kalau sebenarnya masih ada data lain (total tetap dikirim untuk ajakan login).
    """
    guest = user is None
    if guest:
        if query.cursor:
            raise LOGIN_REQUIRED
        # Tamu selalu memakai halaman pertama ukuran default lalu dipotong,
        # jadi tamu & user berbagi entri cache yang sama.
        query = replace(
            query, limit=max(settings.DESTINATION_PAGE_SIZE, settings.GUEST_PREVIEW_LIMIT)
        )

    if query.searched and not query.tokens:
        page = _empty_page(query.limit)  # mis. q="%%": tidak ada kata yang bisa dicari
    else:
        after = decode_cursor(query.cursor, query.sort) if query.cursor else None
        page = cache_get_or_load(
            namespaced_key(HOME_CACHE_NAMESPACE, "destinations", _fingerprint(query)),
            lambda: _load_page(db, query, after),
            ttl=settings.DESTINATION_LIST_CACHE_TTL_SECONDS,
        )

    return _guest_preview(page) if guest else {**page, "locked": False, "message": None}


# ---------------- Ringkasan home ----------------


def _load_summary(db: Session) -> dict:
    categories = crud_home.list_categories_with_count(db)
    districts = crud_home.list_districts_with_count(db)
    featured = crud_home.list_destination_cards(db, sort="rating", limit=settings.FEATURED_LIMIT)
    banners = crud_home.list_banners(db, only_active=True)
    return {
        "banners": [BannerOut.model_validate(b).model_dump(mode="json") for b in banners],
        "categories": [
            {"id": c.id, "name": c.name, "slug": c.slug, "destination_count": n}
            for c, n in categories
        ],
        "districts": [
            {"id": d.id, "name": d.name, "destination_count": n} for d, n in districts
        ],
        # category_id wajib (NOT NULL + FK), jadi jumlah per kategori = total destinasi.
        "total_destinations": sum(n for _, n in categories),
        "featured_destinations": [_card(destination) for destination, _ in featured],
    }


def get_home_summary(db: Session) -> dict:
    """Bagian home yang sama untuk semua pengunjung (di-cache utuh)."""
    return cache_get_or_load(
        namespaced_key(HOME_CACHE_NAMESPACE, "summary"),
        lambda: _load_summary(db),
        ttl=settings.HOME_CACHE_TTL_SECONDS,
    )


def get_home(db: Session, user: UserOut | None) -> dict:
    return {
        "is_authenticated": user is not None,
        **get_home_summary(db),
        "destinations": get_destination_page(db, DestinationQuery(), user),
    }
