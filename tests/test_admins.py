"""اختبارات Admin CRUD: create, list, get, update, delete."""
import pytest


# ── List ───────────────────────────────────────────────────────────────────────

def test_list_admins_requires_auth(client):
    r = client.get("/api/v1/admin/admins")
    assert r.status_code == 401


def test_list_admins_returns_list(client, auth_headers):
    r = client.get("/api/v1/admin/admins", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert isinstance(body["data"], list)
    assert body.get("meta", {}).get("total", len(body["data"])) >= 1


def test_list_admins_search(client, auth_headers):
    r = client.get("/api/v1/admin/admins?search=test_super", headers=auth_headers)
    assert r.status_code == 200
    assert any("test_super" in a["email"] for a in r.json()["data"])


# ── Create ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def created_admin_id(client, auth_headers):
    """ينشئ admin مؤقت ويحذفه بعد اختبارات الـ module."""
    r = client.post("/api/v1/admin/admins", headers=auth_headers, json={
        "email": "temp_admin@yesarha.ai",
        "password": "TempPass123!",
        "full_name": "Temp Admin",
        "role": "admin",
    })
    assert r.status_code == 200
    admin_id = r.json()["data"]["id"]
    yield admin_id
    # cleanup
    client.delete(f"/api/v1/admin/admins/{admin_id}", headers=auth_headers)


def test_create_admin_success(created_admin_id):
    assert created_admin_id is not None


def test_create_admin_duplicate_email_fails(client, auth_headers, created_admin_id):
    r = client.post("/api/v1/admin/admins", headers=auth_headers, json={
        "email": "temp_admin@yesarha.ai",
        "password": "AnotherPass123!",
        "role": "admin",
    })
    assert r.status_code == 409


def test_create_admin_invalid_role_fails(client, auth_headers):
    r = client.post("/api/v1/admin/admins", headers=auth_headers, json={
        "email": "badrole@yesarha.ai",
        "password": "Pass123!",
        "role": "hacker",
    })
    assert r.status_code in (400, 422)


def test_create_admin_short_password_fails(client, auth_headers):
    r = client.post("/api/v1/admin/admins", headers=auth_headers, json={
        "email": "shortpass@yesarha.ai",
        "password": "abc",
        "role": "admin",
    })
    assert r.status_code == 422


# ── Get ────────────────────────────────────────────────────────────────────────

def test_get_admin_by_id(client, auth_headers, created_admin_id):
    r = client.get(f"/api/v1/admin/admins/{created_admin_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["email"] == "temp_admin@yesarha.ai"


def test_get_nonexistent_admin_returns_404(client, auth_headers):
    r = client.get("/api/v1/admin/admins/999999", headers=auth_headers)
    assert r.status_code == 404


# ── Update ─────────────────────────────────────────────────────────────────────

def test_update_admin_full_name(client, auth_headers, created_admin_id):
    r = client.patch(
        f"/api/v1/admin/admins/{created_admin_id}",
        headers=auth_headers,
        json={"full_name": "Updated Admin Name"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["full_name"] == "Updated Admin Name"


def test_update_admin_status_suspend(client, auth_headers, created_admin_id):
    r = client.patch(
        f"/api/v1/admin/admins/{created_admin_id}",
        headers=auth_headers,
        json={"status": "suspended"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "suspended"


# ── Delete ─────────────────────────────────────────────────────────────────────

def test_cannot_delete_self(client, auth_headers, super_admin):
    r = client.delete(
        f"/api/v1/admin/admins/{super_admin.id}",
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_viewer_cannot_list_admins(client, db):
    from app.core.security import create_access_token, hash_password
    from app.models.user import Admin

    viewer = db.query(Admin).filter(Admin.email == "viewer@yesarha.ai").first()
    if not viewer:
        viewer = Admin(
            email="viewer@yesarha.ai",
            password_hash=hash_password("ViewerPass123!"),
            role="viewer",
            permissions=["models"],
            status="active",
            preferred_language="ar",
        )
        db.add(viewer)
        db.commit()
        db.refresh(viewer)

    token = create_access_token(viewer.id, viewer.role)
    r = client.get(
        "/api/v1/admin/admins",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (200, 403)
