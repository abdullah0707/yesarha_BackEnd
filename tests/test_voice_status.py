"""اختبارات Voice Status — لا يحتاج Whisper/XTTS مثبَّتَين."""


def test_voice_status_is_public(client):
    """GET /specialist/voice/status لا يحتاج auth."""
    r = client.get("/api/v1/specialist/voice/status")
    assert r.status_code == 200


def test_voice_status_structure(client):
    r = client.get("/api/v1/specialist/voice/status")
    data = r.json().get("data", r.json())
    assert "whisper_available" in data
    assert "xtts_available" in data
    assert "cuda_available" in data
    assert isinstance(data["whisper_available"], bool)
    assert isinstance(data["xtts_available"], bool)


def test_voice_status_without_specialist_shows_not_created(client):
    r = client.get("/api/v1/specialist/voice/status")
    data = r.json().get("data", r.json())
    specialist_status = data.get("specialist_status", "")
    assert specialist_status in ("not_created", "active", "creating", "inactive", "error")


def test_transcribe_requires_auth(client):
    """POST /specialist/voice/transcribe يحتاج X-API-Key أو Bearer."""
    import io
    dummy_audio = io.BytesIO(b"\x00" * 5000)
    r = client.post(
        "/api/v1/specialist/voice/transcribe",
        files={"audio": ("test.wav", dummy_audio, "audio/wav")},
    )
    assert r.status_code == 401


def test_synthesize_requires_auth(client):
    r = client.post(
        "/api/v1/specialist/voice/synthesize",
        data={"text": "مرحبا", "language": "ar"},
    )
    assert r.status_code == 401


def test_clone_requires_auth(client):
    import io
    dummy = io.BytesIO(b"\x00" * 5000)
    r = client.post(
        "/api/v1/specialist/voice/clone",
        files={"sample": ("sample.wav", dummy, "audio/wav")},
    )
    assert r.status_code == 401


def test_voice_ask_requires_auth(client):
    r = client.post(
        "/api/v1/specialist/voice/ask",
        json={"message": "مرحبا", "language": "ar"},
    )
    assert r.status_code == 401


def test_content_sync_requires_internal_key(client):
    r = client.post(
        "/api/v1/specialist/content/sync",
        json={"content_id": "test-1", "payload": {"section": "intro", "text": "hello"}},
    )
    assert r.status_code == 401


def test_content_sync_with_wrong_key_rejected(client):
    r = client.post(
        "/api/v1/specialist/content/sync",
        headers={"X-Internal-Key": "wrong-key-value"},
        json={"content_id": "test-1", "payload": {"section": "intro", "text": "hello"}},
    )
    assert r.status_code == 401
