"""
Gateway Admin API — لوحة تحكم API Gateway
يتيح للأدمن مراقبة الاستخدام وضبط الحدود لكل Bundle Key.
"""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.responses import success, AppError, ErrorCodes
from app.core.deps import get_current_admin
from app.models.specialist import (
    SpecialistBundle, GatewayRequestLog, GatewayKeyConfig,
)

router = APIRouter(
    prefix="/admin/gateway",
    tags=["Admin - API Gateway"],
    dependencies=[Depends(get_current_admin)],
)


# ─── Schemas ────────────────────────────────────────────────────────────────

class GatewayConfigPatch(BaseModel):
    daily_limit:   Optional[int]      = None
    monthly_limit: Optional[int]      = None
    expires_at:    Optional[datetime] = None
    notes:         Optional[str]      = None
    clear_limits:  bool               = False  # True يُزيل الحدود بالكامل


# ─── Helpers ────────────────────────────────────────────────────────────────

def _today_start() -> datetime:
    now = datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start() -> datetime:
    now = datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _bundle_today_count(db: Session, bundle_id: int) -> int:
    return db.query(GatewayRequestLog).filter(
        GatewayRequestLog.bundle_id == bundle_id,
        GatewayRequestLog.created_at >= _today_start(),
        GatewayRequestLog.status != "rejected",
    ).count()


def _bundle_month_count(db: Session, bundle_id: int) -> int:
    return db.query(GatewayRequestLog).filter(
        GatewayRequestLog.bundle_id == bundle_id,
        GatewayRequestLog.created_at >= _month_start(),
        GatewayRequestLog.status != "rejected",
    ).count()


# ─── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/overview")
def gateway_overview(db: Session = Depends(get_db)):
    """
    ملخص شامل لكل Bundle Keys — الاستخدام اليوم + حالة الحدود.
    """
    bundles = db.query(SpecialistBundle).filter(
        SpecialistBundle.status == "active"
    ).all()

    result = []
    for b in bundles:
        config = db.query(GatewayKeyConfig).filter(
            GatewayKeyConfig.bundle_id == b.id
        ).first()

        today_count = _bundle_today_count(db, b.id)
        month_count = _bundle_month_count(db, b.id)

        # حالة المفتاح
        status = "ok"
        if config:
            now = datetime.utcnow()
            if config.expires_at and now > config.expires_at:
                status = "expired"
            elif config.daily_limit and today_count >= config.daily_limit:
                status = "daily_limit_reached"
            elif config.monthly_limit and month_count >= config.monthly_limit:
                status = "monthly_limit_reached"

        result.append({
            "bundle_id":     b.id,
            "bundle_name":   b.name,
            "api_key_prefix": (b.api_key or "")[:16] + "...",
            "today_requests":  today_count,
            "month_requests":  month_count,
            "total_requests":  b.total_requests or 0,
            "status":          status,
            "config": {
                "daily_limit":   config.daily_limit   if config else None,
                "monthly_limit": config.monthly_limit if config else None,
                "expires_at":    config.expires_at.isoformat() if (config and config.expires_at) else None,
                "notes":         config.notes         if config else None,
            },
        })

    return success({"bundles": result, "total": len(result)})


@router.get("/{bundle_id}/usage")
def bundle_usage(
    bundle_id: int,
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """
    تحليل استخدام Bundle معين خلال N يوم (افتراضي 7).
    يُرجع: طلبات يومية + نسبة النجاح + متوسط وقت الاستجابة + المتخصصين الأكثر استخداماً.
    """
    bundle = db.query(SpecialistBundle).filter(SpecialistBundle.id == bundle_id).first()
    if not bundle:
        raise AppError(ErrorCodes.NOT_FOUND, f"لا توجد حزمة بالمعرّف {bundle_id}", 404)

    since = datetime.utcnow() - timedelta(days=days)
    logs = db.query(GatewayRequestLog).filter(
        GatewayRequestLog.bundle_id == bundle_id,
        GatewayRequestLog.created_at >= since,
    ).all()

    # تجميع يومي
    daily: dict[str, dict] = {}
    specialists_freq: dict[str, int] = {}
    total_ms = 0
    success_count = 0

    for log in logs:
        day_key = log.created_at.strftime("%Y-%m-%d")
        if day_key not in daily:
            daily[day_key] = {"date": day_key, "total": 0, "success": 0, "failed": 0}
        daily[day_key]["total"] += 1
        if log.status == "success":
            daily[day_key]["success"] += 1
            success_count += 1
            total_ms += log.response_ms or 0
        else:
            daily[day_key]["failed"] += 1

        for sp in (log.specialists_used or []):
            specialists_freq[sp] = specialists_freq.get(sp, 0) + 1

    total = len(logs)
    avg_ms = int(total_ms / success_count) if success_count else 0
    top_specialists = sorted(specialists_freq.items(), key=lambda x: x[1], reverse=True)

    return success({
        "bundle_id":   bundle_id,
        "bundle_name": bundle.name,
        "period_days": days,
        "total":       total,
        "success":     success_count,
        "failed":      total - success_count,
        "avg_response_ms": avg_ms,
        "success_rate": round(success_count / total * 100, 1) if total else 0,
        "top_specialists": [{"name": k, "count": v} for k, v in top_specialists[:10]],
        "daily": sorted(daily.values(), key=lambda x: x["date"]),
    })


@router.get("/logs")
def gateway_logs(
    bundle_id: Optional[int] = Query(default=None),
    status:    Optional[str] = Query(default=None),
    page:      int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    عارض مُصفَّح لسجل طلبات Gateway.
    يمكن التصفية بـ bundle_id و status (success/failed/rejected).
    """
    q = db.query(GatewayRequestLog)

    if bundle_id is not None:
        q = q.filter(GatewayRequestLog.bundle_id == bundle_id)
    if status:
        q = q.filter(GatewayRequestLog.status == status)

    total = q.count()
    logs = (
        q.order_by(GatewayRequestLog.created_at.desc())
         .offset((page - 1) * page_size)
         .limit(page_size)
         .all()
    )

    return success({
        "page":      page,
        "page_size": page_size,
        "total":     total,
        "pages":     -(-total // page_size),  # ceiling division
        "logs": [
            {
                "id":              log.id,
                "key_prefix":      log.key_prefix,
                "key_type":        log.key_type,
                "bundle_id":       log.bundle_id,
                "specialist_id":   log.specialist_id,
                "endpoint":        log.endpoint,
                "specialists_used": log.specialists_used,
                "response_ms":     log.response_ms,
                "status":          log.status,
                "ip_address":      log.ip_address,
                "created_at":      log.created_at.isoformat(),
            }
            for log in logs
        ],
    })


@router.patch("/{bundle_id}/config")
def set_bundle_config(
    bundle_id: int,
    body: GatewayConfigPatch,
    db: Session = Depends(get_db),
):
    """
    ضبط حدود وإعدادات Bundle Key.
    - daily_limit / monthly_limit: None = غير محدود
    - expires_at: None = لا ينتهي
    - clear_limits=true: يُزيل جميع الحدود دفعةً واحدة
    """
    bundle = db.query(SpecialistBundle).filter(SpecialistBundle.id == bundle_id).first()
    if not bundle:
        raise AppError(ErrorCodes.NOT_FOUND, f"لا توجد حزمة بالمعرّف {bundle_id}", 404)

    config = db.query(GatewayKeyConfig).filter(
        GatewayKeyConfig.bundle_id == bundle_id
    ).first()

    if not config:
        config = GatewayKeyConfig(bundle_id=bundle_id)
        db.add(config)

    if body.clear_limits:
        config.daily_limit   = None
        config.monthly_limit = None
        config.expires_at    = None
    else:
        if body.daily_limit is not None:
            config.daily_limit = body.daily_limit if body.daily_limit > 0 else None
        if body.monthly_limit is not None:
            config.monthly_limit = body.monthly_limit if body.monthly_limit > 0 else None
        if body.expires_at is not None:
            config.expires_at = body.expires_at

    if body.notes is not None:
        config.notes = body.notes

    config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(config)

    return success({
        "bundle_id":     bundle_id,
        "daily_limit":   config.daily_limit,
        "monthly_limit": config.monthly_limit,
        "expires_at":    config.expires_at.isoformat() if config.expires_at else None,
        "notes":         config.notes,
        "updated_at":    config.updated_at.isoformat(),
    })
