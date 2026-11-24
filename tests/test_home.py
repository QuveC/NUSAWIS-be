import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import fakeredis

from app.core.cache import cache_get_or_load, invalidate_namespace
from app.core.config import settings
from app.core.redis import set_redis_client
from app.crud.crud_home import HOME_CACHE_NAMESPACE
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.home import (
    Banner,
    Category,
    CrowdLevel,
    CrowdStatus,
    DataStatus,
    Destination,
    DestinationPhoto,
    District,
)
from tests.test_auth import login, register

API = "/api/v1/home"
N_PUBLISHED = 25
PHOTO_A = "https://example.com/foto/batu-bolong-senja.jpg"
PHOTO_B = "https://example.com/foto/batu-bolong-pasir.jpg"
PHOTO_C = "https://example.com/foto/wisata-01.jpg"


def seed(db):
    db.add_all(
        [
            Category(id=1, name="Pantai", slug="pantai"),
            Category(id=2, name="Air Terjun", slug="air-terjun"),
            Category(id=3, name="Museum", slug="museum"),  # sengaja kosong
            District(id=1, name="Kuta"),
            District(id=2, name="Ubud"),
        ]
    )
    db.flush()

    dests = []
    for i in range(N_PUBLISHED):
        category_id = 1 if i < 15 else 2
        name = f"{'Pantai' if category_id == 1 else 'Air Terjun'} Wisata {i:02d}"
        description = None
        if i == 0:
            name, description = "Pantai Batu Bolong", "Pantai berpasir hitam, cocok untuk selancar"
        if i == 15:
            name = "Air Terjun Tegenungan"
        # harga bervariasi: 0 = gratis, sebagian belum diisi (None)
        price_min = None if i % 6 == 5 else (i % 5) * 10_000
        price_max = price_min + 5_000 if price_min else None
        facilities = (
            ["Parkir"] + (["Toilet"] if i % 2 == 0 else []) + (["Mushola"] if i % 3 == 0 else [])
        )
        dests.append(
            Destination(
                category_id=category_id,
                district_id=1 + i % 2,
                name=name,
                slug=f"wisata-{i:02d}",
                description=description,
                address=f"Jl. Wisata No. {i}",
                status=DataStatus.PUBLISHED,
                # banyak nilai kembar -> menguji pemutus seri (id) pada cursor
                avg_rating=Decimal("3.0") + Decimal("0.5") * (i % 4),
                rating_count=i % 3,
                # diisi eksplisit: CURRENT_TIMESTAMP SQLite berformat teks lain dari yang
                # di-bind SQLAlchemy (di Postgres keduanya timestamp asli, tidak masalah)
                created_at=datetime(2026, 1, 1) + timedelta(days=i % 10),
                price_min=price_min,
                price_max=price_max,
                facilities=facilities,
            )
        )
    dests.append(
        Destination(
            category_id=1, district_id=1, name="Pantai Rahasia", slug="pantai-rahasia",
            address="Jl. Rahasia", status=DataStatus.DRAFT, avg_rating=Decimal("5.0"),
        )
    )
    db.add_all(dests)
    db.flush()
    db.add_all(
        [
            # sengaja dimasukkan terbalik: urutan harus mengikuti sort_order, bukan urutan insert
            DestinationPhoto(destination_id=dests[0].id, url=PHOTO_B, caption="Pasir", sort_order=1),
            DestinationPhoto(destination_id=dests[0].id, url=PHOTO_A, caption="Senja", sort_order=0),
            DestinationPhoto(destination_id=dests[1].id, url=PHOTO_C, sort_order=0),
        ]
    )
    db.add_all(
        [
            Banner(title="Promo", image_url="https://example.com/a.jpg", sort_order=1),
            Banner(title="Lama", image_url="https://example.com/b.jpg", is_active=False),
        ]
    )
    db.commit()
    return dests


def user_headers(client):
    register(client)
    token = login(client).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def collect_all(client, headers, **params):
    """Telusuri semua halaman lewat next_cursor."""
    slugs, cursor = [], None
    for _ in range(50):
        query = {**params, "limit": 7, **({"cursor": cursor} if cursor else {})}
        r = client.get(f"{API}/destinations", params=query, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        slugs += [d["slug"] for d in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            return slugs, body
    raise AssertionError("pagination tidak berhenti")


# ---------------- Home ----------------


def test_guest_home_is_limited_preview(client, db_session):
    seed(db_session)
    r = client.get(API)
    assert r.status_code == 200
    body = r.json()

    assert body["is_authenticated"] is False
    assert body["total_destinations"] == N_PUBLISHED  # draft tidak dihitung
    assert [(c["slug"], c["destination_count"]) for c in body["categories"]] == [
        ("air-terjun", 10), ("museum", 0), ("pantai", 15),
    ]
    assert {d["name"]: d["destination_count"] for d in body["districts"]} == {"Kuta": 13, "Ubud": 12}
    assert [b["title"] for b in body["banners"]] == ["Promo"]

    featured = body["featured_destinations"]
    assert len(featured) == settings.FEATURED_LIMIT
    assert "pantai-rahasia" not in [d["slug"] for d in featured]
    ratings = [d["avg_rating"] for d in featured]
    assert ratings == sorted(ratings, reverse=True)

    page = body["destinations"]
    assert len(page["items"]) == settings.GUEST_PREVIEW_LIMIT
    assert page["locked"] is True
    assert page["has_more"] is False and page["next_cursor"] is None
    assert page["total"] == N_PUBLISHED
    assert str(N_PUBLISHED) in page["message"]
    # kartu ringkas: tidak membawa data detail
    assert "description" not in page["items"][0] and "facilities" not in page["items"][0]
    assert page["items"][0]["category"]["name"]


def test_logged_in_home_gets_full_first_page(client, db_session):
    seed(db_session)
    headers = user_headers(client)
    body = client.get(API, headers=headers).json()
    assert body["is_authenticated"] is True
    page = body["destinations"]
    assert len(page["items"]) == settings.DESTINATION_PAGE_SIZE
    assert page["locked"] is False and page["message"] is None
    assert page["has_more"] is True and page["next_cursor"]

    r = client.get(
        f"{API}/destinations", params={"cursor": page["next_cursor"]}, headers=headers
    )
    assert r.status_code == 200
    rest = r.json()
    assert len(rest["items"]) == N_PUBLISHED - settings.DESTINATION_PAGE_SIZE
    assert rest["total"] is None  # COUNT hanya di halaman pertama
    assert rest["has_more"] is False and rest["next_cursor"] is None
    all_slugs = [d["slug"] for d in page["items"] + rest["items"]]
    assert len(set(all_slugs)) == N_PUBLISHED


def test_cursor_pagination_every_sort_no_duplicates(client, db_session):
    seed(db_session)
    headers = user_headers(client)
    for sort in ("name", "rating", "newest", "relevance"):
        slugs, _ = collect_all(client, headers, sort=sort)
        assert len(slugs) == len(set(slugs)) == N_PUBLISHED, sort
        assert "pantai-rahasia" not in slugs

    rows = {d.slug: d for d in db_session.query(Destination)}
    names = [rows[s].name for s in collect_all(client, headers, sort="name")[0]]
    assert names == sorted(names)
    dates = [rows[s].created_at for s in collect_all(client, headers, sort="newest")[0]]
    assert dates == sorted(dates, reverse=True)
    ratings = [rows[s].avg_rating for s in collect_all(client, headers, sort="rating")[0]]
    assert ratings == sorted(ratings, reverse=True)


def test_guest_cannot_paginate_and_limit_is_capped(client, db_session):
    seed(db_session)
    r = client.get(f"{API}/destinations", params={"limit": 50})
    assert r.status_code == 200
    assert len(r.json()["items"]) == settings.GUEST_PREVIEW_LIMIT

    cursor = client.get(f"{API}/destinations", headers=user_headers(client)).json()["next_cursor"]
    r = client.get(f"{API}/destinations", params={"cursor": cursor})
    assert r.status_code == 401


def test_invalid_cursor_rejected(client, db_session):
    seed(db_session)
    headers = user_headers(client)
    assert client.get(
        f"{API}/destinations", params={"cursor": "bukan-cursor"}, headers=headers
    ).status_code == 400
    cursor = client.get(
        f"{API}/destinations", params={"sort": "name"}, headers=headers
    ).json()["next_cursor"]
    # cursor dari urutan "name" tidak boleh dipakai untuk urutan "rating"
    assert client.get(
        f"{API}/destinations", params={"sort": "rating", "cursor": cursor}, headers=headers
    ).status_code == 400


# ---------------- Pencarian ----------------


def test_search_prefix_multiword_and_filters(client, db_session):
    seed(db_session)
    headers = user_headers(client)

    def search(**params):
        r = client.get(f"{API}/destinations", params=params, headers=headers)
        assert r.status_code == 200, r.text
        return [d["slug"] for d in r.json()["items"]]

    assert search(q="batu bol") == ["wisata-00"]  # awalan + beberapa kata
    assert search(q="  BATU   Bolong ") == ["wisata-00"]  # dinormalisasi
    assert search(q="pasir hitam") == ["wisata-00"]  # cocok ke deskripsi
    assert search(q="tegenungan") == ["wisata-15"]
    assert search(q="zzz tidak ada") == []
    assert len(search(q="air terjun", district_id=2)) == 5
    assert search(q="wisata", category_id=3) == []
    # relevansi: nama yang diawali kata kunci muncul lebih dulu
    assert search(q="pantai")[0] in {f"wisata-{i:02d}" for i in range(15)}


def test_search_validation(client, db_session):
    seed(db_session)
    assert client.get(f"{API}/destinations", params={"q": "a"}).status_code == 422
    assert client.get(f"{API}/destinations", params={"q": "x" * 101}).status_code == 422
    # tidak ada huruf/angka -> hasil kosong, bukan error / bukan wildcard
    body = client.get(f"{API}/destinations", params={"q": "%%"}).json()
    assert body["items"] == [] and body["total"] == 0
    # q kosong = tidak mencari
    assert client.get(f"{API}/destinations", params={"q": "  "}).json()["total"] == N_PUBLISHED


def test_guest_search_is_preview_with_total(client, db_session):
    seed(db_session)
    body = client.get(f"{API}/destinations", params={"q": "pantai"}).json()
    assert len(body["items"]) == settings.GUEST_PREVIEW_LIMIT
    assert body["total"] == 15 and body["locked"] is True


def test_guest_search_rate_limited(client, db_session):
    seed(db_session)
    for i in range(settings.SEARCH_RATE_LIMIT_GUEST):
        assert client.get(f"{API}/destinations", params={"q": f"cari{i}"}).status_code == 200
    r = client.get(f"{API}/destinations", params={"q": "lagi"})
    assert r.status_code == 429 and "retry-after" in r.headers
    # browsing tanpa pencarian masih boleh
    assert client.get(f"{API}/destinations").status_code == 200


# ---------------- Filter ----------------


def _effective_max(d):
    return d.price_max if d.price_max is not None else d.price_min


FILTER_CASES = [
    ({"max_price": 0}, lambda d: d.price_min is not None and d.price_min <= 0),
    (
        {"min_price": 15_000, "max_price": 30_000},
        lambda d: d.price_min is not None and d.price_min <= 30_000 and _effective_max(d) >= 15_000,
    ),
    ({"min_price": 40_000}, lambda d: _effective_max(d) is not None and _effective_max(d) >= 40_000),
    ({"min_rating": 4.0}, lambda d: (d.avg_rating or 0) >= 4),
    ({"facilities": ["Toilet", "Mushola"]}, lambda d: {"Toilet", "Mushola"} <= set(d.facilities)),
    ({"facilities": "Mushola,Toilet"}, lambda d: {"Toilet", "Mushola"} <= set(d.facilities)),
    (
        {"q": "pantai", "district_id": 1, "facilities": "Toilet", "max_price": 20_000},
        lambda d: "pantai" in d.name.lower()
        and d.district_id == 1
        and "Toilet" in d.facilities
        and d.price_min is not None
        and d.price_min <= 20_000,
    ),
]


def test_filters_match_expected_rows(client, db_session):
    dests = seed(db_session)
    headers = user_headers(client)
    published = [d for d in dests if d.status == DataStatus.PUBLISHED]
    for params, predicate in FILTER_CASES:
        expected = {d.slug for d in published if predicate(d)}
        assert expected, params  # kasus uji harus punya hasil
        slugs, _ = collect_all(client, headers, **params)
        assert sorted(slugs) == sorted(expected), params

        first = client.get(f"{API}/destinations", params=params, headers=headers).json()
        assert first["total"] == len(expected), params


def test_unknown_facility_returns_empty(client, db_session):
    seed(db_session)
    body = client.get(f"{API}/destinations", params={"facilities": "Kolam Renang"}).json()
    assert body["items"] == [] and body["total"] == 0


def test_filter_validation(client, db_session):
    seed(db_session)
    url = f"{API}/destinations"
    assert client.get(url, params={"min_price": 50_000, "max_price": 10_000}).status_code == 422
    assert client.get(url, params={"min_rating": 6}).status_code == 422
    assert client.get(url, params={"max_price": -1}).status_code == 422
    too_many = ",".join(f"f{i}" for i in range(11))
    assert client.get(url, params={"facilities": too_many}).status_code == 422


def test_guest_can_filter_but_sees_preview(client, db_session):
    seed(db_session)
    body = client.get(f"{API}/destinations", params={"facilities": "Toilet"}).json()
    assert body["total"] == 13  # i genap dari 0..24
    assert len(body["items"]) == settings.GUEST_PREVIEW_LIMIT and body["locked"] is True


def test_filter_options(client, db_session):
    dests = seed(db_session)
    published = [d for d in dests if d.status == DataStatus.PUBLISHED]
    body = client.get(f"{API}/filters").json()

    expected_facilities = {
        f: sum(f in d.facilities for d in published) for f in ("Parkir", "Toilet", "Mushola")
    }
    assert {f["name"]: f["destination_count"] for f in body["facilities"]} == expected_facilities
    counts = [f["destination_count"] for f in body["facilities"]]
    assert counts == sorted(counts, reverse=True)

    assert body["price"] == {"min": 0, "max": 45_000}
    assert body["crowd_levels"] == ["SEPI", "SEDANG", "RAMAI"]
    assert {c["slug"] for c in body["categories"]} == {"pantai", "air-terjun", "museum"}
    assert len(body["districts"]) == 2


# ---------------- Filter keramaian ----------------

# (indeks destinasi, level, berapa jam lalu diperbarui). Data > CROWD_STALE_HOURS = kedaluwarsa.
CROWD_SEED = (
    [(i, "SEPI", 0) for i in range(0, 5)]
    + [(i, "SEDANG", 1) for i in range(5, 9)]
    + [(9, "RAMAI", 0), (10, "RAMAI", 2)]
    + [(11, "SEPI", 7), (12, "RAMAI", 30)]  # kedaluwarsa
)


def seed_crowd(db, dests):
    now = datetime.now(timezone.utc)
    for i, level, hours_ago in CROWD_SEED:
        db.add(
            CrowdStatus(
                destination_id=dests[i].id,
                level=CrowdLevel(level),
                current_visitors=10 + i,
                updated_at=now - timedelta(hours=hours_ago),
            )
        )
    db.commit()


def slugs_of(indexes):
    return sorted(f"wisata-{i:02d}" for i in indexes)


def test_crowd_filter(client, db_session):
    seed_crowd(db_session, seed(db_session))
    headers = user_headers(client)

    def found(**params):
        return sorted(collect_all(client, headers, **params)[0])

    assert found(crowd_level="SEPI") == slugs_of(range(0, 5))  # wisata-11 (basi) tidak ikut
    assert found(crowd_level="RAMAI") == slugs_of([9, 10])  # wisata-12 (basi) tidak ikut
    assert found(crowd_level=["SEPI", "SEDANG"]) == slugs_of(range(0, 9))
    assert found(crowd_level="sepi,sedang") == slugs_of(range(0, 9))
    # digabung filter lain: SEDANG di kecamatan 2 (indeks ganjil)
    assert found(crowd_level="SEDANG", district_id=2) == slugs_of([5, 7])
    assert found(crowd_level="SEPI", q="batu bolong") == slugs_of([0])


def test_crowd_info_on_card_and_detail(client, db_session):
    dests = seed(db_session)
    seed_crowd(db_session, dests)
    headers = user_headers(client)
    cards = {}
    cursor = None
    while True:
        params = {"limit": 50, **({"cursor": cursor} if cursor else {})}
        body = client.get(f"{API}/destinations", params=params, headers=headers).json()
        cards.update({c["slug"]: c["crowd"] for c in body["items"]})
        if not (cursor := body["next_cursor"]):
            break

    assert cards["wisata-00"]["level"] == "SEPI" and cards["wisata-00"]["updated_at"]
    assert "current_visitors" not in cards["wisata-00"]  # jumlah pengunjung hanya di detail
    assert cards["wisata-11"]["level"] is None and cards["wisata-11"]["updated_at"]  # basi
    assert cards["wisata-20"] == {"level": None, "updated_at": None}  # belum ada data

    fresh = client.get(f"{API}/destinations/{dests[0].id}").json()["crowd"]
    assert fresh["level"] == "SEPI" and fresh["current_visitors"] == 10
    stale = client.get(f"{API}/destinations/slug/wisata-11").json()["crowd"]
    assert stale["level"] is None and stale["current_visitors"] is None
    none = client.get(f"{API}/destinations/slug/wisata-20").json()["crowd"]
    assert none == {"level": None, "updated_at": None, "current_visitors": None}


def test_crowd_filter_validation_and_guest(client, db_session):
    seed_crowd(db_session, seed(db_session))
    url = f"{API}/destinations"
    r = client.get(url, params={"crowd_level": "PADAT"})
    assert r.status_code == 422 and "PADAT" in r.json()["detail"]

    body = client.get(url, params={"crowd_level": "SEPI,SEDANG"}).json()
    assert body["total"] == 9
    assert len(body["items"]) == settings.GUEST_PREVIEW_LIMIT and body["locked"] is True


def test_crowd_filter_uses_short_cache(client, db_session, redis_client):
    seed_crowd(db_session, seed(db_session))

    def list_key_ttls():
        return {k: redis_client.ttl(k) for k in redis_client.keys("cache:home:*:destinations:*")}

    client.get(f"{API}/destinations", params={"crowd_level": "SEPI"})
    crowd_ttls = list_key_ttls()
    assert len(crowd_ttls) == 1
    assert 0 < max(crowd_ttls.values()) <= 2 * settings.CROWD_LIST_CACHE_TTL_SECONDS

    client.get(f"{API}/destinations")  # tanpa filter keramaian -> cache normal
    normal = {k: t for k, t in list_key_ttls().items() if k not in crowd_ttls}
    assert min(normal.values()) > 2 * settings.CROWD_LIST_CACHE_TTL_SECONDS


# ---------------- Foto ----------------


def test_card_has_cover_photo(client, db_session):
    seed(db_session)
    headers = user_headers(client)
    cards = {
        c["slug"]: c
        for c in client.get(f"{API}/destinations", params={"limit": 50}, headers=headers).json()["items"]
    }
    assert cards["wisata-00"]["cover_photo_url"] == PHOTO_A  # sort_order 0, bukan yang pertama di-insert
    assert cards["wisata-01"]["cover_photo_url"] == PHOTO_C
    assert cards["wisata-02"]["cover_photo_url"] is None
    assert "photos" not in cards["wisata-00"]  # galeri hanya di detail

    # kartu di home (unggulan & daftar) juga membawa foto sampul
    home = client.get(API).json()
    assert all("cover_photo_url" in c for c in home["featured_destinations"])


def test_detail_has_photo_gallery(client, db_session):
    dests = seed(db_session)
    photos = client.get(f"{API}/destinations/{dests[0].id}").json()["photos"]
    assert photos == [{"url": PHOTO_A, "caption": "Senja"}, {"url": PHOTO_B, "caption": "Pasir"}]
    assert client.get(f"{API}/destinations/slug/wisata-01").json()["photos"] == [
        {"url": PHOTO_C, "caption": None}
    ]
    assert client.get(f"{API}/destinations/slug/wisata-02").json()["photos"] == []


def test_photo_url_must_be_http(db_session):
    dests = seed(db_session)
    db_session.add(DestinationPhoto(destination_id=dests[2].id, url="javascript:alert(1)"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------- Cache ----------------


def test_results_cached_until_invalidated(client, db_session):
    seed(db_session)
    assert client.get(API).json()["total_destinations"] == N_PUBLISHED

    db_session.add(
        Destination(
            category_id=1, district_id=1, name="Pantai Baru", slug="pantai-baru",
            address="Jl. Baru", status=DataStatus.PUBLISHED,
        )
    )
    db_session.commit()
    # masih dari cache Redis, DB tidak disentuh
    assert client.get(API).json()["total_destinations"] == N_PUBLISHED

    invalidate_namespace(HOME_CACHE_NAMESPACE)
    assert client.get(API).json()["total_destinations"] == N_PUBLISHED + 1


def test_stale_cache_served_while_another_worker_refreshes(redis_client):
    key = "cache:test:item"
    redis_client.set(key, json.dumps({"exp": time.time() - 1, "data": "lama"}), ex=60)
    redis_client.set(f"cache:lock:{key}", "1", ex=10)  # worker lain sedang memperbarui

    calls = []
    assert cache_get_or_load(key, lambda: calls.append(1) or "baru", ttl=60) == "lama"
    assert calls == []  # tidak ikut query DB

    redis_client.delete(f"cache:lock:{key}")
    assert cache_get_or_load(key, lambda: "baru", ttl=60) == "baru"
    assert cache_get_or_load(key, lambda: "tidak-dipanggil", ttl=60) == "baru"


def test_home_still_works_when_redis_down(client, db_session):
    seed(db_session)
    server = fakeredis.FakeServer()
    server.connected = False
    set_redis_client(fakeredis.FakeRedis(server=server, decode_responses=True))
    r = client.get(API)
    assert r.status_code == 200
    assert r.json()["total_destinations"] == N_PUBLISHED


# ---------------- Lain-lain ----------------


def test_cache_headers(client, db_session):
    seed(db_session)
    assert client.get(API).headers["cache-control"].startswith("public")
    r = client.get(API, headers=user_headers(client))
    assert r.headers["cache-control"].startswith("private")
    assert "Authorization" in r.headers["vary"]


def test_invalid_token_on_public_endpoint_returns_401(client, db_session):
    seed(db_session)
    r = client.get(API, headers={"Authorization": "Bearer token-palsu"})
    assert r.status_code == 401


def test_categories_and_banners_endpoints(client, db_session):
    seed(db_session)
    categories = client.get(f"{API}/categories").json()
    assert {c["slug"]: c["destination_count"] for c in categories}["pantai"] == 15
    assert [b["title"] for b in client.get(f"{API}/banners").json()] == ["Promo"]


def test_get_destination_by_id_and_slug(client, db_session):
    dests = seed(db_session)
    r = client.get(f"{API}/destinations/{dests[0].id}")
    assert r.status_code == 200 and r.json()["name"] == "Pantai Batu Bolong"
    assert r.json()["facilities"] == ["Parkir", "Toilet", "Mushola"]
    assert client.get(f"{API}/destinations/slug/wisata-01").status_code == 200
    # draft tidak terlihat publik
    assert client.get(f"{API}/destinations/{dests[-1].id}").status_code == 404
    assert client.get(f"{API}/destinations/slug/pantai-rahasia").status_code == 404
    assert client.get(f"{API}/destinations/bukan-uuid").status_code == 422
