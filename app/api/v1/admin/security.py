"""
Security Manager API — /admin/security
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional

from app.core.deps import get_current_admin
from app.core.responses import success, AppError, ErrorCodes
from app.db.session import get_db
from app.models.user import Admin
from app.models.security import SecurityEvent, BlockedIP, TrustedIP
from app.services.security_service import unblock_ip

router = APIRouter(prefix="/admin/security", tags=["Admin - Security"])


def _serialize_event(e: SecurityEvent) -> dict:
    return {
        "id":           e.id,
        "ip":           e.ip,
        "event_type":   e.event_type,
        "severity":     e.severity,
        "path":         e.path,
        "user_agent":   e.user_agent,
        "details":      e.details,
        "auto_blocked": e.auto_blocked,
        "created_at":   e.created_at.isoformat() if e.created_at else None,
    }


def _serialize_block(b: BlockedIP) -> dict:
    return {
        "id":               b.id,
        "ip":               b.ip,
        "reason":           b.reason,
        "event_type":       b.event_type,
        "severity":         b.severity,
        "is_active":        b.is_active,
        "blocked_at":       b.blocked_at.isoformat() if b.blocked_at else None,
        "expires_at":       b.expires_at.isoformat() if b.expires_at else None,
        "unblocked_at":     b.unblocked_at.isoformat() if b.unblocked_at else None,
        "unblocked_by_id":  b.unblocked_by_id,
        "is_ai_decision":   bool(b.is_ai_decision),
        "ai_reasoning":     b.ai_reasoning,
        "ai_review_status": b.ai_review_status,
    }


def _serialize_trusted(t: TrustedIP) -> dict:
    return {
        "id":         t.id,
        "ip":         t.ip,
        "label":      t.label,
        "added_by":   t.added_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    from datetime import datetime
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_events    = db.query(SecurityEvent).count()
    events_today    = db.query(SecurityEvent).filter(SecurityEvent.created_at >= today_start).count()
    critical_events = db.query(SecurityEvent).filter(SecurityEvent.severity == "critical").count()
    active_blocks   = db.query(BlockedIP).filter(BlockedIP.is_active == True).count()
    ai_pending      = db.query(BlockedIP).filter(
        BlockedIP.is_active == True,
        BlockedIP.is_ai_decision == True,
        BlockedIP.ai_review_status == "pending",
    ).count()
    trusted_count   = db.query(TrustedIP).count()

    by_type: dict = {}
    for row in db.query(SecurityEvent.event_type, SecurityEvent.id).all():
        by_type[row.event_type] = by_type.get(row.event_type, 0) + 1

    return success({
        "total_events":    total_events,
        "events_today":    events_today,
        "critical_events": critical_events,
        "active_blocks":   active_blocks,
        "ai_pending":      ai_pending,
        "trusted_count":   trusted_count,
        "by_type":         by_type,
    })


# ── Events ────────────────────────────────────────────────────────────────────

@router.get("/events")
def list_events(
    limit:    int = Query(50, le=200),
    offset:   int = Query(0, ge=0),
    severity: str = Query(""),
    ip:       str = Query(""),
    db: Session = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    q = db.query(SecurityEvent).order_by(desc(SecurityEvent.created_at))
    if severity:
        q = q.filter(SecurityEvent.severity == severity)
    if ip:
        q = q.filter(SecurityEvent.ip == ip)
    total  = q.count()
    events = q.offset(offset).limit(limit).all()
    return success({"total": total, "items": [_serialize_event(e) for e in events]})


# ── Blocks ────────────────────────────────────────────────────────────────────

@router.get("/blocks")
def list_blocks(
    active_only: bool = Query(True),
    ai_only:     bool = Query(False),
    limit:  int  = Query(50, le=200),
    offset: int  = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    q = db.query(BlockedIP).order_by(desc(BlockedIP.blocked_at))
    if active_only:
        q = q.filter(BlockedIP.is_active == True)
    if ai_only:
        q = q.filter(BlockedIP.is_ai_decision == True, BlockedIP.ai_review_status == "pending")
    total  = q.count()
    blocks = q.offset(offset).limit(limit).all()
    return success({"total": total, "items": [_serialize_block(b) for b in blocks]})


@router.delete("/blocks/{ip}")
def unblock(
    ip: str,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    ok = unblock_ip(db, ip, admin.id)
    if not ok:
        raise AppError(ErrorCodes.NOT_FOUND, f"العنوان {ip} غير محجوب أو غير موجود", 404)
    return success({"message": f"تم رفع الحجب عن {ip}", "ip": ip})


@router.post("/blocks")
def manual_block(
    payload: dict,
    db: Session = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    from app.services.security_service import log_event, block_ip
    ip     = payload.get("ip", "").strip()
    reason = payload.get("reason", "حجب يدوي من الأدمن")
    if not ip:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "ip مطلوب", 400)
    log_event(db, ip, "manual", details=reason, auto_blocked=False)
    block_ip(db, ip, "manual", reason)
    return success({"message": f"تم حجب {ip}", "ip": ip})


# ── AI Review ─────────────────────────────────────────────────────────────────

class AIReviewRequest(BaseModel):
    action: str  # confirm | cancel


@router.patch("/blocks/{ip}/ai-review")
def review_ai_block(
    ip: str,
    payload: AIReviewRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    """مراجعة قرار AI — تأكيد الحجب أو إلغاؤه."""
    if payload.action not in ("confirm", "cancel"):
        raise AppError(ErrorCodes.VALIDATION_ERROR, "action يجب أن يكون confirm أو cancel", 400)

    blocked = db.query(BlockedIP).filter(
        BlockedIP.ip == ip,
        BlockedIP.is_ai_decision == True,
    ).first()
    if not blocked:
        raise AppError(ErrorCodes.NOT_FOUND, f"لا يوجد قرار AI للـ IP {ip}", 404)

    if payload.action == "confirm":
        blocked.ai_review_status = "confirmed"
        blocked.is_active        = True
        db.commit()
        return success({"message": f"تم تأكيد حجب {ip}", "ip": ip, "status": "confirmed"})
    else:
        from app.services.security_service import unblock_ip, add_trusted_ip
        blocked.ai_review_status = "cancelled"
        unblock_ip(db, ip, admin.id)
        return success({"message": f"تم إلغاء حجب {ip}", "ip": ip, "status": "cancelled"})


# ── Trusted IPs ───────────────────────────────────────────────────────────────

class AddTrustedIPRequest(BaseModel):
    ip: str
    label: Optional[str] = None


@router.get("/trusted")
def list_trusted(
    db: Session = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    trusted = db.query(TrustedIP).order_by(desc(TrustedIP.created_at)).all()
    return success({"items": [_serialize_trusted(t) for t in trusted]})


@router.post("/trusted")
def add_trusted(
    payload: AddTrustedIPRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    ip    = payload.ip.strip()
    label = payload.label or "IP موثوق"
    if not ip:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "ip مطلوب", 400)

    from app.services.security_service import add_trusted_ip
    add_trusted_ip(db, ip, label, admin.id)
    return success({"message": f"تمت إضافة {ip} للـ whitelist", "ip": ip})


@router.delete("/trusted/{ip}")
def remove_trusted(
    ip: str,
    db: Session = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    from app.services.security_service import remove_trusted_ip
    ok = remove_trusted_ip(db, ip)
    if not ok:
        raise AppError(ErrorCodes.NOT_FOUND, f"الـ IP {ip} غير موجود في الـ whitelist", 404)
    return success({"message": f"تمت إزالة {ip} من الـ whitelist", "ip": ip})
