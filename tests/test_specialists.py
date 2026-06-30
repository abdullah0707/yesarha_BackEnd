"""اختبارات Specialist CRUD: list, create, get, delete, specializations.
Background setup (Ollama pull) مُعطَّل — نختبر فقط HTTP responses وحالة DB."""
import pytest
from unittest.mock import patch, MagicMock


# ── Specializations ────────────────────────────────────────────────────────────

def test_list_specializations_no_auth(client):
    r = client.get("/api/v1/admin/specialists/specializations")
    assert r.status_code == 401


def test_list_specializations(client, auth_headers):
    r = client.get("/api/v1/admin/specialists/specializations", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    specs = data.get("data", data)
    assert isinstance(specs, (list, dict))


# ── List Specialists ───────────────────────────────────────────────────────────

def test_list_specialists_requires_auth(client):
    r = client.get("/api/v1/admin/specialists")
    assert r.status_code == 401


def test_list_specialists(client, auth_headers):
    r = client.get("/api/v1/admin/specialists", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json().get("data", []), list)


# ── Create Specialist ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def created_specialist_id(client, auth_headers):
    """ينشئ specialist ويحذفه بعد الاختبارات — background setup مُعطَّل."""
    with patch("app.api.v1.admin.specialists._background_specialist_setup"):
        r = client.post("/api/v1/admin/specialists", headers=auth_headers, json={
            "name":         "test-edu-specialist",
            "display_name": "Test Education",
            "specialization": "education",
        })
    assert r.status_code in (200, 201), r.text
    spec_id = r.json()["data"]["id"]
    yield spec_id
    with patch("app.api.v1.admin.specialists._background_specialist_setup"):
        client.delete(f"/api/v1/admin/specialists/{spec_id}", headers=auth_headers)


def test_create_specialist_returns_id(created_specialist_id):
    assert isinstance(created_specialist_id, int)


def test_create_specialist_duplicate_name_fails(client, auth_headers):
    with patch("app.api.v1.admin.specialists._background_specialist_setup"):
        r = client.post("/api/v1/admin/specialists", headers=auth_headers, json={
            "name":           "test-edu-specialist",
            "display_name":   "Duplicate",
            "specialization": "education",
        })
    assert r.status_code in (409, 400)


def test_create_specialist_invalid_specialization(client, auth_headers):
    with patch("app.api.v1.admin.specialists._background_specialist_setup"):
        r = client.post("/api/v1/admin/specialists", headers=auth_headers, json={
            "name":           "test-bad-spec",
            "display_name":   "Bad",
            "specialization": "unicorn",
        })
    assert r.status_code in (400, 422)


# ── Get Specialist ─────────────────────────────────────────────────────────────

def test_get_specialist_by_id(client, auth_headers, created_specialist_id):
    r = client.get(f"/api/v1/admin/specialists/{created_specialist_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "test-edu-specialist"


def test_get_nonexistent_specialist(client, auth_headers):
    r = client.get("/api/v1/admin/specialists/999999", headers=auth_headers)
    assert r.status_code == 404


# ── Patch Specialist ───────────────────────────────────────────────────────────

def test_patch_specialist_display_name(client, auth_headers, created_specialist_id):
    r = client.patch(
        f"/api/v1/admin/specialists/{created_specialist_id}",
        headers=auth_headers,
        json={"display_name": "Updated Edu"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["display_name"] == "Updated Edu"


def test_patch_specialist_system_prompt(client, auth_headers, created_specialist_id):
    r = client.patch(
        f"/api/v1/admin/specialists/{created_specialist_id}",
        headers=auth_headers,
        json={"system_prompt": "أنت مساعد تعليمي ذكي."},
    )
    assert r.status_code == 200


# ── Active Specialist Fixtures ─────────────────────────────────────────────────

def test_active_specialist_fixture_created(active_specialist):
    assert active_specialist.status == "active"
    assert active_specialist.api_key is not None


def test_public_ask_without_key_returns_401(client):
    r = client.post("/api/v1/specialist/ask", json={
        "message": "مرحبا",
    })
    assert r.status_code == 401


def test_public_ask_with_invalid_key_returns_401(client):
    r = client.post(
        "/api/v1/specialist/ask",
        headers={"X-API-Key": "yesk_code_invalid_key_here"},
        json={"message": "مرحبا"},
    )
    assert r.status_code == 401
