from app.core.cache import user_cache_key
from app.core.config import settings

API = "/api/v1"
PASSWORD = "Rahasia#123"


def register(client, email="tester@example.com", password=PASSWORD, **extra):
    return client.post(
        f"{API}/auth/register",
        json={"name": "Tester", "email": email, "password": password, **extra},
    )


def login(client, email="tester@example.com", password=PASSWORD):
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_me(client, redis_client):
    r = register(client, email="Tester@Example.com")
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "tester@example.com"
    assert body["role"] == "USER"
    assert "password" not in body and "password_hash" not in body

    r = login(client)
    assert r.status_code == 200
    data = r.json()
    assert data["token_type"] == "bearer"
    assert data["refresh_token"]
    assert data["user"]["name"] == "Tester"
    assert r.headers["cache-control"] == "no-store"

    r = client.get(f"{API}/users/me", headers=auth(data["access_token"]))
    assert r.status_code == 200
    assert r.json()["email"] == "tester@example.com"
    # profil tersimpan di cache Redis
    assert redis_client.get(user_cache_key(body["id"])) is not None


def test_register_rejects_weak_password_and_role_injection(client):
    assert register(client, password="password").status_code == 422
    assert register(client, role="admin").status_code == 422


def test_register_duplicate_email(client):
    assert register(client).status_code == 201
    assert register(client, email="TESTER@example.com").status_code == 409


def test_login_wrong_password_and_unknown_email_same_message(client):
    register(client)
    r1 = login(client, password="Salah#1234")
    r2 = login(client, email="nobody@example.com")
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"]


def test_account_lockout(client):
    register(client)
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
        assert login(client, password="Salah#1234").status_code == 401
    r = login(client)  # password benar pun ditolak saat terkunci
    assert r.status_code == 429
    assert "retry-after" in r.headers


def test_login_rate_limit_per_ip(client):
    for _ in range(settings.LOGIN_RATE_LIMIT):
        assert login(client, email="x@example.com").status_code in (401, 429)
    r = login(client, email="other@example.com")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_register_rate_limit_per_ip(client):
    for i in range(settings.REGISTER_RATE_LIMIT):
        assert register(client, email=f"u{i}@example.com").status_code == 201
    assert register(client, email="last@example.com").status_code == 429


def test_refresh_rotation_and_reuse_detection(client):
    register(client)
    tokens = login(client).json()

    r = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new_tokens = r.json()

    # refresh token lama tidak bisa dipakai lagi
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    # reuse terdeteksi -> semua sesi dicabut, termasuk token baru
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]})
    assert r.status_code == 401


def test_access_token_cannot_be_used_as_refresh(client):
    register(client)
    tokens = login(client).json()
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert r.status_code == 401
    r = client.get(f"{API}/users/me", headers=auth(tokens["refresh_token"]))
    assert r.status_code == 401


def test_logout_revokes_tokens(client):
    register(client)
    tokens = login(client).json()
    r = client.post(
        f"{API}/auth/logout",
        headers=auth(tokens["access_token"]),
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert r.status_code == 200
    assert client.get(f"{API}/users/me", headers=auth(tokens["access_token"])).status_code == 401
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_change_password_revokes_sessions(client):
    register(client)
    tokens = login(client).json()
    r = client.post(
        f"{API}/users/me/password",
        headers=auth(tokens["access_token"]),
        json={"current_password": PASSWORD, "new_password": "BaruLagi#456"},
    )
    assert r.status_code == 200
    assert client.get(f"{API}/users/me", headers=auth(tokens["access_token"])).status_code == 401
    assert login(client).status_code == 401
    new = login(client, password="BaruLagi#456")
    assert new.status_code == 200
    assert client.get(f"{API}/users/me", headers=auth(new.json()["access_token"])).status_code == 200


def test_update_me_invalidates_cache(client):
    register(client)
    token = login(client).json()["access_token"]
    client.get(f"{API}/users/me", headers=auth(token))
    r = client.patch(f"{API}/users/me", headers=auth(token), json={"name": "Nama Baru"})
    assert r.status_code == 200
    assert client.get(f"{API}/users/me", headers=auth(token)).json()["name"] == "Nama Baru"


def test_swagger_token_endpoint(client):
    register(client)
    r = client.post(
        f"{API}/auth/token", data={"username": "tester@example.com", "password": PASSWORD}
    )
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_tampered_token_rejected(client):
    register(client)
    token = login(client).json()["access_token"]
    assert client.get(f"{API}/users/me", headers=auth(token[:-2] + "xx")).status_code == 401
