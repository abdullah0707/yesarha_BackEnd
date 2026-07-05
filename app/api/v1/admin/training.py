"""
Training Pipeline API — إدارة ومراقبة دورة التدريب التلقائي
"""
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, AppError, ErrorCodes
from app.models.specialist import (
    SpecialistModel, ModelPerformanceLog, TrainingSession, CoreTask,
)

router = APIRouter(
    prefix="/admin/training",
    tags=["Admin - Training Pipeline"],
    dependencies=[Depends(get_current_admin)],
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _enrich_session(s: TrainingSession, name_map: dict[int, str]) -> dict:
    return {
        "id":                 s.id,
        "model_id":           s.model_id,
        "model_name":         name_map.get(s.model_id or 0, f"model_{s.model_id}"),
        "session_type":       s.session_type,
        "status":             s.status,
        "before_score":       s.before_score,
        "after_score":        s.after_score,
        "improvement_percent":s.improvement_percent,
        "data_sources_count": len(s.data_sources or []),
        "started_at":         s.started_at.isoformat()   if s.started_at   else None,
        "completed_at":       s.completed_at.isoformat() if s.completed_at else None,
        "created_at":         s.created_at.isoformat(),
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/sessions")
def list_training_sessions(
    model_id:     Optional[int] = Query(default=None),
    session_type: Optional[str] = Query(default=None),
    limit:        int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """كل جلسات التدريب — مُصفَّحة ومُفصَّلة"""
    q = db.query(TrainingSession)
    if model_id:
        q = q.filter(TrainingSession.model_id == model_id)
    if session_type:
        q = q.filter(TrainingSession.session_type == session_type)

    sessions = q.order_by(TrainingSession.created_at.desc()).limit(limit).all()

    model_ids = {s.model_id for s in sessions if s.model_id}
    name_map  = {}
    if model_ids:
        rows = db.query(SpecialistModel.id, SpecialistModel.name).filter(
            SpecialistModel.id.in_(model_ids)
        ).all()
        name_map = {r.id: r.name for r in rows}

    # إحصاءات سريعة
    total   = db.query(TrainingSession).count()
    w_after = db.query(TrainingSession).filter(
        TrainingSession.after_score.isnot(None)
    ).count()
    avg_improvement = db.query(
        func.avg(TrainingSession.improvement_percent)
    ).filter(TrainingSession.improvement_percent.isnot(None)).scalar()

    return success({
        "sessions": [_enrich_session(s, name_map) for s in sessions],
        "stats": {
            "total":              total,
            "measured":           w_after,
            "avg_improvement_pct": round(float(avg_improvement), 1) if avg_improvement else None,
        },
    })


@router.get("/{specialist_id}/quality-trend")
def quality_trend(
    specialist_id: int,
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """
    trend جودة نموذج خلال N يوم.
    يُرجع: متوسط quality_score يومياً + المشاكل الأكثر تكراراً.
    """
    model = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not model:
        raise AppError(ErrorCodes.NOT_FOUND, f"النموذج {specialist_id} غير موجود", 404)

    since = datetime.utcnow() - timedelta(days=days)
    logs = db.query(ModelPerformanceLog).filter(
        ModelPerformanceLog.model_id == specialist_id,
        ModelPerformanceLog.created_at >= since,
        ModelPerformanceLog.quality_score.isnot(None),
    ).order_by(ModelPerformanceLog.created_at.asc()).all()

    # تجميع يومي
    daily: dict[str, dict] = {}
    issue_freq: dict[str, int] = {}

    for lg in logs:
        day = lg.created_at.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"date": day, "count": 0, "score_sum": 0.0}
        daily[day]["count"]     += 1
        daily[day]["score_sum"] += lg.quality_score

        for issue in (lg.issues_detected or []):
            issue_freq[issue] = issue_freq.get(issue, 0) + 1

    daily_trend = []
    for day, d in sorted(daily.items()):
        daily_trend.append({
            "date":       day,
            "avg_score":  round(d["score_sum"] / d["count"], 3),
            "count":      d["count"],
        })

    top_issues = sorted(issue_freq.items(), key=lambda x: x[1], reverse=True)[:10]

    # آخر جلسة تدريب
    last_session = db.query(TrainingSession).filter(
        TrainingSession.model_id == specialist_id
    ).order_by(TrainingSession.created_at.desc()).first()

    return success({
        "specialist_id":   specialist_id,
        "specialist_name": model.display_name,
        "period_days":     days,
        "total_scored":    len(logs),
        "overall_avg":     round(sum(d["score_sum"] for d in daily.values()) / max(len(logs), 1), 3),
        "daily_trend":     daily_trend,
        "top_issues":      [{"issue": k, "count": v} for k, v in top_issues],
        "last_session": _enrich_session(last_session, {specialist_id: model.name}) if last_session else None,
    })


@router.post("/{specialist_id}/trigger")
def trigger_training(
    specialist_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    تشغيل يدوي لدورة التحليل والإصلاح لنموذج محدد.
    يعمل في الخلفية — النتيجة تظهر في /training/sessions.
    """
    model = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not model:
        raise AppError(ErrorCodes.NOT_FOUND, f"النموذج {specialist_id} غير موجود", 404)

    if model.status != "active":
        raise AppError(ErrorCodes.FORBIDDEN, "النموذج غير نشط — لا يمكن تدريبه", 403)

    from app.core.intelligence.auto_monitor import core_monitor
    from app.db.session import SessionLocal

    def _run():
        _db = SessionLocal()
        try:
            _model = _db.query(SpecialistModel).filter(
                SpecialistModel.id == specialist_id
            ).first()
            if _model:
                core_monitor._run_quality_scoring()
                core_monitor._evaluate_model(_model, _db)
        finally:
            _db.close()

    background_tasks.add_task(_run)

    return success({
        "message": f"✅ بدأ Core في تحليل '{model.display_name}' — النتيجة في /training/sessions",
        "specialist_id": specialist_id,
    })


@router.get("/queue")
def training_queue(db: Session = Depends(get_db)):
    """
    النماذج التي تحتاج تدريباً — إما لم تُدرَّب قط أو مضى على تدريبها أكثر من 7 أيام.
    """
    week_ago = datetime.utcnow() - timedelta(days=7)

    models = db.query(SpecialistModel).filter(
        SpecialistModel.status == "active",
        (SpecialistModel.last_trained_at.is_(None)) |
        (SpecialistModel.last_trained_at < week_ago),
    ).all()

    queue = []
    for m in models:
        # متوسط quality_score آخر 50 طلب
        recent_logs = db.query(ModelPerformanceLog).filter(
            ModelPerformanceLog.model_id == m.id,
            ModelPerformanceLog.quality_score.isnot(None),
        ).order_by(ModelPerformanceLog.created_at.desc()).limit(50).all()

        avg_q = None
        if recent_logs:
            avg_q = round(sum(lg.quality_score for lg in recent_logs) / len(recent_logs), 3)

        days_since = None
        if m.last_trained_at:
            days_since = (datetime.utcnow() - m.last_trained_at).days

        queue.append({
            "id":               m.id,
            "name":             m.name,
            "display_name":     m.display_name,
            "specialization":   m.specialization,
            "avg_quality":      avg_q,
            "days_since_train": days_since,
            "last_trained_at":  m.last_trained_at.isoformat() if m.last_trained_at else None,
            "next_training_at": m.next_training_at.isoformat() if m.next_training_at else None,
            "priority": "high" if (avg_q and avg_q < 0.6) else
                        "medium" if (avg_q and avg_q < 0.75) else "normal",
        })

    queue.sort(key=lambda x: (
        x["priority"] != "high",
        x["priority"] != "medium",
        -(x["days_since_train"] or 999),
    ))

    return success({"queue": queue, "total": len(queue)})
