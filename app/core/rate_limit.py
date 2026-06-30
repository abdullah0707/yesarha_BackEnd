"""Rate limiting for public-facing endpoints (X-API-Key callers)."""
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request

from app.core.config import settings


def _rate_limit_key(request: Request) -> str:
    """يحدّد عداد الـ rate limit حسب X-API-Key إن وُجد، وإلا حسب IP.
    X-Internal-Key مستبعد عمداً لمنع تداخل الـ bucket مع نظام specialist."""
    api_key = request.headers.get("X-API-Key")
    return api_key or get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)
DEFAULT_RATE_LIMIT = f"{settings.RATE_LIMIT_PER_MINUTE}/minute"
