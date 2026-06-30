"""اختبارات Auth: login, refresh, me, password change."""
import pytest


# ── Login ──────────────────────────────────────────────────────────────────────

def test_login_success(client):
    r = client.post("/api/v1/auth/login", json={
        "email": "test_super@yesarha.ai",
        "password": "TestAdmin123!",
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["admin"]["role"] == "super_admin"


def test_login_wrong_password(client):
    r = client.post("/api/v1/auth/login", json={
        "email": "test_super@yesarha.ai",
        "password": "WrongPassword!",
    })
    assert r.status_code == 401


def test_login_unknown_email(client):
    r = client.post("/api/v1/auth/login", json={
        "email": "nobody@yesarha.ai",
        "password": "TestAdmin123!",
    })
    assert r.status_code == 401


def test_login_invalid_email_format(client):
    r = client.post("/api/v1/auth/login", json={
        "email": "not-an-email",
        "password": "TestAdmin123!",
    })
    assert r.status_code == 422


def test_login_missing_fields(client):
    r = client.post("/api/v1/auth/login", json={})
    assert r.status_code == 422


# ── Token Refresh ──────────────────────────────────────────────────────────────

def test_refresh_token(client):
    login = client.post("/api/v1/auth/login", json={
        "email": "test_super@yesarha.ai",
        "password": "TestAdmin123!",
    })
    refresh_token = login.json()["data"]["refresh_token"]

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r.status_code == 200
    assert "access_token" in r.json()["data"]


def test_refresh_with_invalid_token(client):
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "invalid.token.value"})
    assert r.status_code == 401


def test_refresh_with_access_token_fails(client, admin_token):
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": admin_token})
    assert r.status_code == 401


# ── /auth/me ───────────────────────────────────────────────────────────────────

def test_me_returns_admin_info(client, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["email"] == "test_super@yesarha.ai"
    assert data["role"] == "super_admin"


def test_me_without_token_returns_401(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_me_with_invalid_token_returns_401(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token"})
    assert r.status_code == 401


# ── PATCH /auth/me ─────────────────────────────────────────────────────────────

def test_update_full_name(client, auth_headers):
    r = client.patch(
        "/api/v1/auth/me",
        params={"full_name": "Updated Name"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["full_name"] == "Updated Name"


def test_update_preferred_language(client, auth_headers):
    r = client.patch(
        "/api/v1/auth/me",
        params={"preferred_language": "en"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["preferred_language"] == "en"
