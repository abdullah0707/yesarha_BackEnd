"""اختبارات endpoints العامة: health + manifest."""


def test_health_returns_200(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200


def test_health_contains_status(client):
    r = client.get("/api/v1/health")
    data = r.json()
    assert "status" in data or data.get("success") is True


def test_manifest_requires_auth(client):
    """Dashboard manifest محمي بـ JWT."""
    r = client.get("/api/v1/dashboard/manifest")
    assert r.status_code == 401


def test_manifest_returns_200_with_auth(client, auth_headers):
    r = client.get("/api/v1/dashboard/manifest", headers=auth_headers)
    assert r.status_code == 200


def test_manifest_has_app_name(client, auth_headers):
    r = client.get("/api/v1/dashboard/manifest", headers=auth_headers)
    body = r.json()
    data = body.get("data", body)
    assert isinstance(data, dict)
    assert "system" in data or "name" in data


def test_docs_available(client):
    r = client.get("/docs")
    assert r.status_code == 200


def test_unknown_route_returns_404(client):
    r = client.get("/api/v1/this-does-not-exist")
    assert r.status_code == 404
