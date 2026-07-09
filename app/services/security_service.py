"""
Security Manager — يرصد الأنماط المشبوهة ويحجب IPs تلقائياً.

قواعد الحجب (الحجب دائم — لا يُرفع إلا بمراجعة يدوية من الأدمن):
  brute_force   → 5 محاولات تسجيل دخول فاشلة في 5 دقائق
  rate_limit    → 120 طلب/دقيقة
  scanner       → طلب مسارات مشبوهة (wp-admin، phpMyAdmin)
  flood_404     → 20 صفحة غير موجودة في 5 دقائق
"""
import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from threading import Lock
from typing import Optional

logger = logging.getLogger("yesarha.security")

# ── Scanner path patterns ─────────────────────────────────────────────────────
_SCANNER_RE = re.compile(
    r"/(wp-admin|wp-login|wp-content|phpMyAdmin|phpmyadmin|adminer|"
    r"admin/config|\.env|\.git|\.svn|config\.php|backup|shell\b|cmd\b|"
    r"console|actuator|\.aws|web\.config|app\.config|"
    r"setup\.php|install\.php|upgrade\.php)",
    re.IGNORECASE,
)

# ── Severity per event type ───────────────────────────────────────────────────
_SEVERITY = {
    "brute_force": "high",
    "rate_limit":  "medium",
    "scanner":     "critical",
    "flood_404":   "high",
}

# ── Thresholds ────────────────────────────────────────────────────────────────
_BRUTE_FORCE_MAX     = 5
_BRUTE_FORCE_WINDOW  = 300   # 5 دقائق
_RATE_LIMIT_MAX      = 120
_RATE_LIMIT_WINDOW   = 60    # 1 دقيقة
_FLOOD_404_MAX       = 20
_FLOOD_404_WINDOW    = 300   # 5 دقائق

# ── In-memory counters ────────────────────────────────────────────────────────
_request_times:    dict[str, list[float]] = defaultdict(list)
_login_fail_times: dict[str, list[float]] = defaultdict(list)
_404_times:        dict[str, list[float]] = defaultdict(list)
_lock = Lock()

# ── Blocked IP cache (fast path — no DB) ─────────────────────────────────────
_blocked_cache: dict[str, Optional[float]] = {}  # ip → None (دائم)
_cache_lock = Lock()

# ── Trusted IP cache (Whitelist — لا تُطبَّق عليها أي قواعد) ─────────────────
_trusted_cache: set[str] = set()
_trusted_lock = Lock()


def _now() -> float:
    return time.time()


def _prune(times: list[float], window: float) -> list[float]:
    cutoff = _now() - window
    return [t for t in times if t > cutoff]


def _cache_add(ip: str, expires_at: Optional[datetime]) -> None:
    with _cache_lock:
        _blocked_cache[ip] = expires_at.timestamp() if expires_at else None


def _cache_remove(ip: str) -> None:
    with _cache_lock:
        _blocked_cache.pop(ip, None)


def is_ip_trusted(ip: str) -> bool:
    """فحص سريع — هل الـ IP في الـ whitelist؟"""
    with _trusted_lock:
        return ip in _trusted_cache


def _trusted_add(ip: str) -> None:
    with _trusted_lock:
        _trusted_cache.add(ip)


def _trusted_remove(ip: str) -> None:
    with _trusted_lock:
        _trusted_cache.discard(ip)


def load_trusted_ips_from_db(db) -> int:
    """يُستدعى عند بدء التطبيق — يُحمّل IPs الموثوقة في الذاكرة."""
    try:
        from app.models.security import TrustedIP
        trusted = db.query(TrustedIP).all()
        loaded = 0
        for t in trusted:
            _trusted_add(t.ip)
            loaded += 1
        logger.info(f"[Security] Loaded {loaded} trusted IPs into cache")
        return loaded
    except Exception as e:
        logger.warning(f"[Security] Failed to load trusted IPs: {e}")
        return 0


def is_ip_blocked_fast(ip: str) -> bool:
    """فحص سريع — الـ IP الموثوق لا يُحجب أبداً."""
    if is_ip_trusted(ip):
        return False
    with _cache_lock:
        return ip in _blocked_cache


def load_blocked_ips_from_db(db) -> int:
    """يُستدعى عند بدء التطبيق — يُحمّل كل الحجوبات النشطة (دائمة حتى الرفع اليدوي)."""
    try:
        from app.models.security import BlockedIP
        blocks = db.query(BlockedIP).filter(BlockedIP.is_active == True).all()
        loaded = 0
        for b in blocks:
            _cache_add(b.ip, None)
            loaded += 1
        logger.info(f"[Security] Loaded {loaded} active IP blocks into cache")
        return loaded
    except Exception as e:
        logger.warning(f"[Security] Failed to load blocked IPs: {e}")
        return 0


# ── Logging ───────────────────────────────────────────────────────────────────

def log_event(
    db,
    ip: str,
    event_type: str,
    path: str = "",
    user_agent: str = "",
    details: str = "",
    auto_blocked: bool = False,
) -> None:
    try:
        from app.models.security import SecurityEvent
        ev = SecurityEvent(
            ip=ip,
            event_type=event_type,
            severity=_SEVERITY.get(event_type, "medium"),
            path=path[:500] if path else None,
            user_agent=user_agent[:300] if user_agent else None,
            details=details[:1000] if details else None,
            auto_blocked=auto_blocked,
        )
        db.add(ev)
        db.commit()
        logger.warning(f"[Security] {event_type.upper()} from {ip} | {details[:100]}")
    except Exception as e:
        logger.error(f"[Security] Failed to log event: {e}")
        try:
            db.rollback()
        except Exception:
            pass


# ── Blocking ──────────────────────────────────────────────────────────────────

def block_ip(db, ip: str, event_type: str, reason: str) -> None:
    """يحجب IP بشكل دائم — لا يُرفع إلا بمراجعة يدوية من الأدمن."""
    try:
        from app.models.security import BlockedIP
        severity = _SEVERITY.get(event_type, "high")

        existing = db.query(BlockedIP).filter(BlockedIP.ip == ip).first()
        if existing:
            existing.is_active       = True
            existing.reason          = reason
            existing.event_type      = event_type
            existing.severity        = severity
            existing.blocked_at      = datetime.utcnow()
            existing.expires_at      = None  # دائم حتى المراجعة
            existing.unblocked_at    = None
            existing.unblocked_by_id = None
        else:
            db.add(BlockedIP(
                ip=ip, reason=reason, event_type=event_type,
                severity=severity, expires_at=None,
            ))
        db.commit()
        _cache_add(ip, None)
        logger.warning(f"[Security] BLOCKED {ip} | {event_type} | permanent until admin review")
    except Exception as e:
        logger.error(f"[Security] Failed to block IP: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def unblock_ip(db, ip: str, admin_id: int) -> bool:
    try:
        from app.models.security import BlockedIP
        blocked = db.query(BlockedIP).filter(
            BlockedIP.ip == ip, BlockedIP.is_active == True,
        ).first()
        if not blocked:
            return False
        blocked.is_active       = False
        blocked.unblocked_at    = datetime.utcnow()
        blocked.unblocked_by_id = admin_id
        db.commit()
        _cache_remove(ip)
        logger.info(f"[Security] UNBLOCKED {ip} by admin {admin_id}")
        return True
    except Exception as e:
        logger.error(f"[Security] Failed to unblock IP: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False


# ── Detection ─────────────────────────────────────────────────────────────────

def check_rate_limit(ip: str) -> bool:
    """True إذا تجاوز IP حد الطلبات. الـ IPs الموثوقة معفاة دائماً."""
    if is_ip_trusted(ip):
        return False
    with _lock:
        times = _prune(_request_times[ip], _RATE_LIMIT_WINDOW)
        times.append(_now())
        _request_times[ip] = times
        return len(times) > _RATE_LIMIT_MAX


def is_scanner_path(path: str) -> bool:
    return bool(_SCANNER_RE.search(path))


def record_login_failure(db, ip: str, path: str = "/auth/login") -> None:
    """يُستدعى من endpoint تسجيل الدخول عند كل محاولة فاشلة."""
    if is_ip_trusted(ip):
        return
    with _lock:
        times = _prune(_login_fail_times[ip], _BRUTE_FORCE_WINDOW)
        times.append(_now())
        _login_fail_times[ip] = times
        count = len(times)

    if count >= _BRUTE_FORCE_MAX:
        log_event(db, ip, "brute_force", path,
                  details=f"{count} failed logins in {_BRUTE_FORCE_WINDOW}s",
                  auto_blocked=True)
        block_ip(db, ip, "brute_force", f"محاولات تسجيل دخول مكثفة ({count} مرة)")
        with _lock:
            _login_fail_times[ip] = []


def record_login_success(ip: str) -> None:
    with _lock:
        _login_fail_times.pop(ip, None)


def record_404(db, ip: str, path: str, user_agent: str = "") -> None:
    """يُستدعى من الـ middleware بعد استجابة 404."""
    if is_ip_trusted(ip):
        return
    with _lock:
        times = _prune(_404_times[ip], _FLOOD_404_WINDOW)
        times.append(_now())
        _404_times[ip] = times
        count = len(times)

    if count >= _FLOOD_404_MAX:
        log_event(db, ip, "flood_404", path, user_agent,
                  details=f"{count} × 404 in {_FLOOD_404_WINDOW}s",
                  auto_blocked=True)
        block_ip(db, ip, "flood_404", f"فيضان طلبات 404 ({count} مرة)")
        with _lock:
            _404_times[ip] = []


# ── Trusted IP management ─────────────────────────────────────────────────────

def add_trusted_ip(db, ip: str, label: str, admin_id: int) -> None:
    from app.models.security import TrustedIP
    existing = db.query(TrustedIP).filter(TrustedIP.ip == ip).first()
    if not existing:
        db.add(TrustedIP(ip=ip, label=label, added_by=admin_id))
        db.commit()
    _trusted_add(ip)
    _cache_remove(ip)  # رفع الحجب إن كان محجوباً
    logger.info(f"[Security] TRUSTED {ip} | {label}")


def remove_trusted_ip(db, ip: str) -> bool:
    from app.models.security import TrustedIP
    t = db.query(TrustedIP).filter(TrustedIP.ip == ip).first()
    if not t:
        return False
    db.delete(t)
    db.commit()
    _trusted_remove(ip)
    logger.info(f"[Security] UNTRUSTED {ip}")
    return True


# ── AI block ──────────────────────────────────────────────────────────────────

def block_ip_ai(db, ip: str, reasoning: str, severity: str = "high") -> None:
    """حجب مؤقت بقرار AI — بانتظار مراجعة الأدمن."""
    if is_ip_trusted(ip):
        return
    try:
        from app.models.security import BlockedIP
        existing = db.query(BlockedIP).filter(BlockedIP.ip == ip).first()
        if existing:
            existing.is_active       = True
            existing.reason          = "قرار AI — بانتظار المراجعة"
            existing.event_type      = "ai_detected"
            existing.severity        = severity
            existing.blocked_at      = datetime.utcnow()
            existing.expires_at      = None
            existing.unblocked_at    = None
            existing.unblocked_by_id = None
            existing.is_ai_decision  = True
            existing.ai_reasoning    = reasoning
            existing.ai_review_status = "pending"
        else:
            db.add(BlockedIP(
                ip=ip,
                reason="قرار AI — بانتظار المراجعة",
                event_type="ai_detected",
                severity=severity,
                expires_at=None,
                is_ai_decision=True,
                ai_reasoning=reasoning,
                ai_review_status="pending",
            ))
        db.commit()
        _cache_add(ip, None)
        logger.warning(f"[AI Security] BLOCKED {ip} | {reasoning[:80]}")
    except Exception as e:
        logger.error(f"[AI Security] Failed to block IP: {e}")
        try:
            db.rollback()
        except Exception:
            pass
