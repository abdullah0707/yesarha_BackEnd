"""اختبارات Core Settings: GET/PUT prompt + config + reset."""


def test_get_core_settings_requires_auth(client):
    r = client.get("/api/v1/admin/core-settings")
    assert r.status_code == 401


def test_get_core_settings(client, auth_headers):
    r = client.get("/api/v1/admin/core-settings", headers=auth_headers)
    assert r.status_code == 200
    data = r.json().get("data", {})
    assert "system_prompt" in data or "prompt" in data or "config" in data


def test_get_tools(client, auth_headers):
    r = client.get("/api/v1/admin/core-settings/tools", headers=auth_headers)
    assert r.status_code == 200
    body = r.json().get("data", r.json())
    tools = body.get("tools", body) if isinstance(body, dict) else body
    assert isinstance(tools, list)
    assert len(tools) > 0


def test_update_system_prompt(client, auth_headers):
    new_prompt = "أنت Yesarha Core — نظام ذكاء اصطناعي داخلي. [test]"
    r = client.put(
        "/api/v1/admin/core-settings/prompt",
        headers=auth_headers,
        json={"system_prompt": new_prompt},
    )
    assert r.status_code == 200


def test_update_system_prompt_persists(client, auth_headers):
    r = client.get("/api/v1/admin/core-settings", headers=auth_headers)
    assert r.status_code == 200
    body = r.json().get("data", {})
    prompt = body.get("system_prompt", body.get("prompt", ""))
    assert "[test]" in prompt


def test_reset_system_prompt(client, auth_headers):
    r = client.post("/api/v1/admin/core-settings/prompt/reset", headers=auth_headers)
    assert r.status_code == 200


def test_update_config(client, auth_headers):
    r = client.put(
        "/api/v1/admin/core-settings/config",
        headers=auth_headers,
        json={"temperature": 0.7, "max_tokens": 2048},
    )
    assert r.status_code == 200


def test_update_prompt_requires_auth(client):
    r = client.put(
        "/api/v1/admin/core-settings/prompt",
        json={"system_prompt": "hack"},
    )
    assert r.status_code == 401
