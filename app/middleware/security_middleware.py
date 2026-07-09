"""
Security Middleware — يعمل على كل طلب HTTP.

الفحوصات (بالترتيب):
  1. IP محجوب → 403 فوري (in-memory، بدون DB)
  2. مسار ماسح (scanner) → حجب فوري + 403
  3. Rate limit → حجب + 429
  4. بعد الاستجابة: 404 flooding tracking

استخراج IP الحقيقي (X-Forwarded-For):
  إذا جاء الطلب من proxy موثوق (nginx، Docker bridge، localhost)
  يُستخرج الـ IP الأصلي من X-Real-IP أو X-Forwarded-For.
  لا نقرأ هذه الـ headers إذا جاء الطلب من IP غير موثوق
  (منع تزوير IP عبر header مزور من client مباشر).

Trusted proxies الافتراضية:
  127.0.0.0/8      — localhost
  ::1/128          — IPv6 localhost
  10.0.0.0/8       — private A
  172.16.0.0/12    — private B (Docker bridge: 172.17–172.31.x.x)
  192.168.0.0/16   — private C
  + ما يُحدَّد في TRUSTED_PROXIES بـ .env
"""
import ipaddress
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("yesarha.security")

_SKIP_PREFIXES = ("/docs", "/redoc", "/openapi.json")

# ── Trusted proxy networks (يُبنى مرة واحدة عند أول طلب) ──────────────────────
_DEFAULT_TRUSTED_CIDRS = [
    "127.0.0.0/8",      # localhost
    "::1/128",          # IPv6 localhost
    "10.0.0.0/8",       # private A
    "172.16.0.0/12",    # private B — Docker bridge يقع هنا دائماً
    "192.168.0.0/16",   # private C
]

_trusted_networks: list = []
_networks_built = False


def _build_trusted_networks() -> None:
    global _trusted_networks, _networks_built
    if _networks_built:
        return
    from app.core.config import settings
    extra_raw = getattr(settings, "TRUSTED_PROXIES", "").strip()
    extra = [c.strip() for c in extra_raw.split(",") if c.strip()] if extra_raw else []
    nets = []
    for cidr in _DEFAULT_TRUSTED_CIDRS + extra:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning(f"[Security] Invalid trusted proxy CIDR ignored: {cidr!r}")
    _trusted_networks = nets
    _networks_built = True
    logger.info(f"[Security] Trusted proxy networks loaded: {len(nets)}"
                + (f" + {len(extra)} from TRUSTED_PROXIES" if extra else ""))


def _is_trusted_proxy(ip: str) -> bool:
    """هل الـ IP هو proxy موثوق يُسمح بقراءة forwarding headers منه؟"""
    if not ip or ip == "unknown":
        return False
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _trusted_networks)
    except ValueError:
        return False


def _extract_real_ip(request: Request) -> str:
    """
    استخراج IP المستخدم الحقيقي من الطلب.

    المنطق:
    - إذا كان مصدر الاتصال المباشر (TCP) proxy موثوقاً:
        1. X-Real-IP — يضعه nginx مباشرة (أحادي، موثوق)
        2. X-Forwarded-For — قائمة IPs، نأخذ الأول (الـ client الأصلي)
    - وإلا: نستخدم IP الاتصال المباشر كما هو
    """
    _build_trusted_networks()

    direct_ip = (request.client.host if request.client else None) or "unknown"

    if not _is_trusted_proxy(direct_ip):
        return direct_ip

    # X-Real-IP: nginx يضعه مباشرة — أوثق من X-Forwarded-For
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        try:
            ipaddress.ip_address(real_ip)
            return real_ip
        except ValueError:
            logger.debug(f"[Security] Invalid X-Real-IP value ignored: {real_ip!r}")

    # X-Forwarded-For: نأخذ أول إدخال (الـ client الأصلي قبل الـ proxies)
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            try:
                ipaddress.ip_address(first)
                return first
            except ValueError:
                logger.debug(f"[Security] Invalid X-Forwarded-For first entry ignored: {first!r}")

    return direct_ip


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.services.security_service import (
            is_ip_blocked_fast,
            check_rate_limit,
            is_scanner_path,
            log_event,
            block_ip,
            record_404,
        )
        from app.db.session import SessionLocal

        ip   = _extract_real_ip(request)
        path = request.url.path
        ua   = request.headers.get("user-agent", "")

        # تخطي فحص docs
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        # 1. IP محجوب؟ → 403 فوري بدون DB
        if is_ip_blocked_fast(ip):
            return JSONResponse(
                status_code=403,
                content={"status": "error", "message": "تم حظر هذا العنوان. تواصل مع المسؤول."},
            )

        # 2. مسار ماسح → حجب فوري
        if is_scanner_path(path):
            db = SessionLocal()
            try:
                log_event(db, ip, "scanner", path, ua,
                          details=f"Scanner probe: {path}", auto_blocked=True)
                block_ip(db, ip, "scanner", f"فحص مسارات مشبوهة: {path[:100]}")
            finally:
                db.close()
            return JSONResponse(
                status_code=403,
                content={"status": "error", "message": "طلب مرفوض"},
            )

        # 3. Rate limit
        if check_rate_limit(ip):
            db = SessionLocal()
            try:
                log_event(db, ip, "rate_limit", path, ua,
                          details="Exceeded 120 req/min", auto_blocked=True)
                block_ip(db, ip, "rate_limit", "تجاوز حد معدل الطلبات (120/دقيقة)")
            finally:
                db.close()
            return JSONResponse(
                status_code=429,
                content={"status": "error", "message": "كثير من الطلبات، حاول بعد قليل"},
            )

        # تنفيذ الطلب
        response = await call_next(request)

        # 4. تتبع 404 بعد الاستجابة
        if response.status_code == 404:
            db = SessionLocal()
            try:
                record_404(db, ip, path, ua)
            finally:
                db.close()

        return response
