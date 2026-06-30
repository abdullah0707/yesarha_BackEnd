"""اختبارات Monitor: overview, tasks, trigger-check."""
from unittest.mock import patch


def test_monitor_overview_requires_auth(client):
    r = client.get("/api/v1/admin/monitor/overview")
    assert r.status_code == 401


def test_monitor_overview(client, auth_headers):
    r = client.get("/api/v1/admin/monitor/overview", headers=auth_headers)
    assert r.status_code == 200
    data = r.json().get("data", {})
    assert isinstance(data, dict)


def test_monitor_tasks(client, auth_headers):
    r = client.get("/api/v1/admin/monitor/tasks", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    items = body.get("data", body.get("items", []))
    assert isinstance(items, list)


def test_monitor_weekly_report(client, auth_headers):
    r = client.get("/api/v1/admin/monitor/weekly-report", headers=auth_headers)
    assert r.status_code == 200


def test_trigger_check_requires_auth(client):
    r = client.post("/api/v1/admin/monitor/trigger-check")
    assert r.status_code == 401


def test_trigger_check(client, auth_headers):
    with patch("app.core.intelligence.auto_monitor.core_monitor._check_all_models"):
        r = client.post("/api/v1/admin/monitor/trigger-check", headers=auth_headers)
    assert r.status_code == 200


def test_dashboard_summary(client, auth_headers):
    r = client.get("/api/v1/admin/dashboard/summary", headers=auth_headers)
    assert r.status_code == 200
    data = r.json().get("data", {})
    assert isinstance(data, dict)
