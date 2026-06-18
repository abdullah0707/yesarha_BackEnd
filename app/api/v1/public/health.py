from fastapi import APIRouter
import requests

from app.core.config import settings
from app.core.responses import success
from app.db.session import engine

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check():

    db_status = "online"
    try:
        with engine.connect():
            pass
    except Exception:
        db_status = "offline"

    ollama_status = "online"
    try:
        resp = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=2)
        if resp.status_code != 200:
            ollama_status = "offline"
    except Exception:
        ollama_status = "offline"

    overall = "online" if db_status == "online" else "degraded"

    return success({
        "api": "online",
        "database": db_status,
        "ollama": ollama_status,
        "status": overall,
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION
    })
