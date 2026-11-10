from decimal import Decimal

from app.models.home import Banner, Category, DataStatus, Destination, District

API = "/api/v1/home"


def seed(db):
    cat = Category(id=1, name="Pantai", slug="pantai")
    dis = District(id=1, name="Kuta")
    db.add_all([cat, dis])
    db.flush()
    rows = [
        ("Pantai A", "pantai-a", DataStatus.PUBLISHED, Decimal("4.50")),
        ("Pantai B", "pantai-b", DataStatus.PUBLISHED, Decimal("4.90")),
        ("Pantai Draft", "pantai-draft", DataStatus.DRAFT, Decimal("5.00")),
    ]
    dests = [
        Destination(
            category_id=1, district_id=1, name=n, slug=s, address="Jl. Pantai",
            status=st, avg_rating=r, lat=Decimal("-8.7"), lng=Decimal("115.1"),
            facilities=["parkir", "toilet"], open_hours={"senin": "08-17"},
        )
        for n, s, st, r in rows
    ]
    db.add_all(dests)
    db.add(Banner(title="Promo", image_url="https://example.com/a.jpg", sort_order=1))
    db.commit()
    return dests


def test_home_featured_only_published_sorted_by_rating(client, db_session):
    seed(db_session)
    r = client.get(API)
    assert r.status_code == 200
    body = r.json()
    assert [d["slug"] for d in body["featured_destinations"]] == ["pantai-b", "pantai-a"]
    assert body["featured_destinations"][0]["category"]["name"] == "Pantai"
    assert body["featured_destinations"][0]["facilities"] == ["parkir", "toilet"]
    assert len(body["banners"]) == 1


def test_list_and_search_destinations(client, db_session):
    seed(db_session)
    assert len(client.get(f"{API}/destinations").json()) == 2
    r = client.get(f"{API}/destinations", params={"q": "pantai a"})
    assert [d["slug"] for d in r.json()] == ["pantai-a"]
    # wildcard tidak dianggap pola
    assert client.get(f"{API}/destinations", params={"q": "%"}).json() == []


def test_get_destination_by_id_and_slug(client, db_session):
    dests = seed(db_session)
    r = client.get(f"{API}/destinations/{dests[0].id}")
    assert r.status_code == 200 and r.json()["name"] == "Pantai A"
    assert client.get(f"{API}/destinations/slug/pantai-b").status_code == 200
    # draft tidak terlihat publik
    assert client.get(f"{API}/destinations/{dests[2].id}").status_code == 404
    assert client.get(f"{API}/destinations/slug/pantai-draft").status_code == 404
    assert client.get(f"{API}/destinations/bukan-uuid").status_code == 422
