"""اختبارات أمنية: token tampering, SQL injection patterns, path traversal."""


# ── Token Security ─────────────────────────────────────────────────────────────

def test_tampered_token_rejected(client):
    r = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.tampered"},
    )
    assert r.status_code == 401


def test_empty_bearer_rejected(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_non_bearer_scheme_rejected(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401


# ── Input Validation ───────────────────────────────────────────────────────────

def test_sql_injection_in_login_email(client):
    """SQL injection في email لا يُسبَّب 500."""
    r = client.post("/api/v1/auth/login", json={
        "email": "' OR '1'='1",
        "password": "anything",
    })
    assert r.status_code in (401, 422)


def test_xss_payload_in_name_field(client, auth_headers, db):
    """XSS payload في full_name يُخزَّن كنص خام فقط، لا يُنفَّذ."""
    r = client.patch(
        "/api/v1/auth/me",
        params={"full_name": "<script>alert('xss')</script>"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    stored = r.json()["data"]["full_name"]
    assert stored == "<script>alert('xss')</script>"


def test_oversized_system_prompt_accepted(client, auth_headers):
    """System prompt كبير (لكن ضمن حدود معقولة) يجب أن يُقبَل."""
    big_prompt = "أ" * 10_000
    r = client.put(
        "/api/v1/admin/core-settings/prompt",
        headers=auth_headers,
        json={"system_prompt": big_prompt},
    )
    assert r.status_code == 200


def test_path_traversal_in_specialist_name(client, auth_headers):
    """اسم نموذج يحتوي على path traversal يُرفَض بـ 422 (Pydantic validation)."""
    from unittest.mock import patch
    with patch("app.api.v1.admin.specialists._background_specialist_setup"):
        r = client.post("/api/v1/admin/specialists", headers=auth_headers, json={
            "name":           "../../etc/passwd",
            "display_name":   "Evil",
            "specialization": "code",
        })
    assert r.status_code == 422, f"Expected 422 got {r.status_code}: {r.text}"


def test_specialist_name_with_spaces_rejected(client, auth_headers):
    """اسم نموذج يحتوي على مسافات يُرفَض."""
    from unittest.mock import patch
    with patch("app.api.v1.admin.specialists._background_specialist_setup"):
        r = client.post("/api/v1/admin/specialists", headers=auth_headers, json={
            "name":           "bad name here",
            "display_name":   "Bad",
            "specialization": "code",
        })
    assert r.status_code == 422


def test_valid_specialist_name_accepted(client, auth_headers):
    """اسم نموذج صالح (حروف إنجليزية وشرطات) يُقبَل."""
    from unittest.mock import patch
    with patch("app.api.v1.admin.specialists._background_specialist_setup"):
        r = client.post("/api/v1/admin/specialists", headers=auth_headers, json={
            "name":           "valid-specialist-01",
            "display_name":   "Valid Specialist",
            "specialization": "code",
        })
    assert r.status_code in (200, 201, 409)


def test_internal_key_wrong_value_rejected(client):
    r = client.post(
        "/api/v1/specialist/content/sync",
        headers={"X-Internal-Key": "incorrect_key_value"},
        json={"content_id": "x", "payload": {}},
    )
    assert r.status_code == 401


def test_internal_key_correct_value_accepted(client):
    """مفتاح صحيح يقبله النظام (مع payload صالح)."""
    r = client.post(
        "/api/v1/specialist/content/sync",
        headers={"X-Internal-Key": "test-internal-key-12345"},
        json={
            "content_id": "security-test-content",
            "title": "Test Chapter",
            "payload": {"introduction": "هذا اختبار أمني.", "goals": "التحقق من صحة المفتاح."},
        },
    )
    assert r.status_code == 200
