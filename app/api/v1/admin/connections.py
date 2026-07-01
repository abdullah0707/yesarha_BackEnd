"""
Connections Health API — فحص صحة الخدمات الداخلية في الوقت الفعلي.
يُستدعى من لوحة التحكم لعرض حالة: PostgreSQL / Ollama / Redis / SearXNG
"""
import time
import requests
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.security import require_admin
from app.db.session import engine

router = APIRouter(prefix="/admin/connections", tags=["Connections Health"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ping_ollama(url: str) -> dict:
    start = time.perf_counter()
    try:
        resp = requests.get(f"{url.rstrip('/')}/api/tags", timeout=4)
        latency = int((time.perf_counter() - start) * 1000)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            return {
                "status": "online",
                "latency_ms": latency,
                "details": {
                    "models_count": len(models),
                    "models": [m.get("name") for m in models[:8]],
                },
            }
        return {"status": "offline", "latency_ms": latency, "details": {"http_status": resp.status_code}}
    except requests.exceptions.ConnectionError:
        return {"status": "offline", "latency_ms": None, "details": {"error": "Connection refused"}}
    except requests.exceptions.Timeout:
        return {"status": "offline", "latency_ms": None, "details": {"error": "Timeout after 4s"}}
    except Exception as e:
        return {"status": "offline", "latency_ms": None, "details": {"error": str(e)[:120]}}


def _ping_redis(url: str) -> dict:
    start = time.perf_counter()
    try:
        import redis as redis_lib
        r = redis_lib.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        r.ping()
        latency = int((time.perf_counter() - start) * 1000)
        info = r.info("server")
        return {
            "status": "online",
            "latency_ms": latency,
            "details": {
                "version": info.get("redis_version"),
                "uptime_seconds": info.get("uptime_in_seconds"),
                "connected_clients": r.info("clients").get("connected_clients"),
                "used_memory_human": r.info("memory").get("used_memory_human"),
            },
        }
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        return {"status": "offline", "latency_ms": latency, "details": {"error": str(e)[:120]}}


def _ping_searxng(url: str) -> dict:
    start = time.perf_counter()
    try:
        # SearXNG health — يدعم /healthz أو نبحث بكلمة بسيطة
        resp = requests.get(
            f"{url.rstrip('/')}/search",
            params={"q": "test", "format": "json"},
            headers={"User-Agent": "YesarhaCore/HealthCheck"},
            timeout=5,
        )
        latency = int((time.perf_counter() - start) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "status": "online",
                "latency_ms": latency,
                "details": {
                    "results_count": len(data.get("results", [])),
                    "query": data.get("query"),
                },
            }
        return {"status": "degraded", "latency_ms": latency, "details": {"http_status": resp.status_code}}
    except requests.exceptions.ConnectionError:
        return {"status": "offline", "latency_ms": None, "details": {"error": "Connection refused"}}
    except requests.exceptions.Timeout:
        return {"status": "offline", "latency_ms": None, "details": {"error": "Timeout after 5s"}}
    except Exception as e:
        return {"status": "offline", "latency_ms": None, "details": {"error": str(e)[:120]}}


def _ping_database() -> dict:
    start = time.perf_counter()
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()")).fetchone()
        latency = int((time.perf_counter() - start) * 1000)
        version = str(result[0]) if result else "unknown"
        # نُظهر فقط الجزء الأول من version string
        short_version = version.split(",")[0] if version else "unknown"
        return {
            "status": "online",
            "latency_ms": latency,
            "details": {"version": short_version},
        }
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        return {"status": "offline", "latency_ms": latency, "details": {"error": str(e)[:120]}}


def _get_service_urls() -> dict[str, str]:
    """يقرأ الـ URLs من runtime_cfg مع fallback لـ settings"""
    try:
        from app.services.runtime_config import runtime_cfg
        return {
            "ollama":   runtime_cfg.get_ollama_url(),
            "redis":    runtime_cfg.get_redis_url(),
            "searxng":  runtime_cfg.get_searxng_url(),
        }
    except Exception:
        from app.core.config import settings
        return {
            "ollama":  settings.OLLAMA_BASE_URL,
            "redis":   settings.REDIS_URL,
            "searxng": settings.SEARXNG_URL,
        }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/health", summary="فحص صحة كل الخدمات الداخلية")
def check_all_health(admin=Depends(require_admin)):
    urls = _get_service_urls()

    db_check     = _ping_database()
    ollama_check = _ping_ollama(urls["ollama"])
    redis_check  = _ping_redis(urls["redis"])
    searxng_check = _ping_searxng(urls["searxng"])

    services = [
        {
            "key":    "database",
            "label":  "PostgreSQL",
            "url":    "internal",          # DATABASE_URL لا يُعرض للأمان
            **db_check,
        },
        {
            "key":    "ollama",
            "label":  "Ollama",
            "url":    urls["ollama"],
            **ollama_check,
        },
        {
            "key":    "redis",
            "label":  "Redis",
            "url":    urls["redis"],
            **redis_check,
        },
        {
            "key":    "searxng",
            "label":  "SearXNG",
            "url":    urls["searxng"],
            **searxng_check,
        },
    ]

    statuses = [s["status"] for s in services]
    if all(s == "online" for s in statuses):
        overall = "healthy"
    elif any(s == "online" for s in statuses):
        overall = "degraded"
    else:
        overall = "down"

    return {
        "status": "success",
        "data": {
            "overall": overall,
            "services": services,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    }


@router.get("/health/{service}", summary="فحص صحة خدمة واحدة")
def check_single_service(service: str, admin=Depends(require_admin)):
    urls = _get_service_urls()
    valid = {"database", "ollama", "redis", "searxng"}
    if service not in valid:
        return {"status": "error", "message": f"خدمة غير معروفة. المتاح: {', '.join(valid)}"}

    result = {
        "database": _ping_database,
        "ollama":   lambda: _ping_ollama(urls["ollama"]),
        "redis":    lambda: _ping_redis(urls["redis"]),
        "searxng":  lambda: _ping_searxng(urls["searxng"]),
    }[service]()

    return {
        "status": "success",
        "data": {
            "key": service,
            "url": urls.get(service, "internal"),
            **result,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    }
