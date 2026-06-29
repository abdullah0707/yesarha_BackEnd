"""
Monitor API — تقارير المراقبة التلقائية لـ Core
عرض حالة النماذج، التقارير الأسبوعية، CoreTasks، وإطلاق مراقبة يدوية
"""
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success
from app.models.specialist import (
    SpecialistModel, ModelPerformanceLog,
    CoreTask, TrainingSession
)

router = APIRouter(prefix="/admin/monitor", tags=["Admin - Auto Monitor"])


@router.get("/overview")
def monitor_overview(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """لوحة المراقبة الرئيسية — نظرة شاملة على صحة كل النماذج"""
    models = db.query(SpecialistModel).all()

    overview = []
    total_requests = 0
    needs_attention = []
    healthy_count = 0

    for m in models:
        # أداء آخر 24 ساعة
        day_ago = datetime.utcnow() - timedelta(hours=24)
        recent_logs = db.query(ModelPerformanceLog).filter(
            ModelPerformanceLog.model_id == m.id,
            ModelPerformanceLog.created_at >= day_ago
        ).all()

        recent_success = sum(1 for l in recent_logs if l.status == "success")
        recent_rate = (recent_success / len(recent_logs)) if recent_logs else None

        all_logs_count = db.query(ModelPerformanceLog).filter(
            ModelPerformanceLog.model_id == m.id
        ).count()

        total_requests += all_logs_count

        # تحديد الحالة الصحية
        if m.status != "active":
            health = "inactive"
        elif recent_rate is None:
            health = "no_data"
        elif recent_rate >= 0.85:
            health = "healthy"
            healthy_count += 1
        elif recent_rate >= 0.70:
            health = "warning"
        else:
            health = "critical"
            needs_attention.append(m.name)

        # آخر تدريب
        days_since_training = None
        if m.last_trained_at:
            delta = datetime.utcnow() - m.last_trained_at
            days_since_training = delta.days

        overview.append({
            "id": m.id,
            "name": m.name,
            "display_name": m.display_name,
            "specialization": m.specialization,
            "status": m.status,
            "base_model": m.base_model,
            "health": health,
            "success_rate_all_time": round((m.success_rate or 0) * 100, 1),
            "success_rate_24h": round(recent_rate * 100, 1) if recent_rate is not None else None,
            "requests_24h": len(recent_logs),
            "requests_total": all_logs_count,
            "days_since_training": days_since_training,
            "next_training_at": m.next_training_at.isoformat() if m.next_training_at else None,
        })

    # آخر CoreTasks
    recent_tasks = db.query(CoreTask).order_by(
        CoreTask.created_at.desc()
    ).limit(10).all()

    return success({
        "summary": {
            "total_models": len(models),
            "active_models": sum(1 for m in models if m.status == "active"),
            "healthy_models": healthy_count,
            "needs_attention": needs_attention,
            "total_requests_24h": sum(
                o["requests_24h"] for o in overview
            ),
            "total_requests_all_time": total_requests,
        },
        "models": overview,
        "recent_tasks": [{
            "id": t.id,
            "type": t.task_type,
            "status": t.status,
            "model_id": t.target_model_id,
            "output": t.output_data,
            "created_at": t.created_at.isoformat(),
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        } for t in recent_tasks],
    })


@router.get("/model/{model_id}/history")
def model_performance_history(
    model_id: int,
    days: int = 7,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """تاريخ أداء نموذج محدد — يُستخدم لرسم الرسوم البيانية"""
    model = db.query(SpecialistModel).filter(
        SpecialistModel.id == model_id
    ).first()
    if not model:
        return success({"error": "model not found"})

    since = datetime.utcnow() - timedelta(days=days)
    logs = db.query(ModelPerformanceLog).filter(
        ModelPerformanceLog.model_id == model_id,
        ModelPerformanceLog.created_at >= since
    ).order_by(ModelPerformanceLog.created_at.asc()).all()

    # تجميع يومي
    daily: dict = {}
    for log in logs:
        day = log.created_at.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"total": 0, "success": 0, "avg_ms": 0, "ms_sum": 0}
        daily[day]["total"] += 1
        if log.status == "success":
            daily[day]["success"] += 1
        daily[day]["ms_sum"] += log.response_ms or 0

    daily_stats = []
    for day, data in sorted(daily.items()):
        daily_stats.append({
            "date": day,
            "total": data["total"],
            "success_rate": round(data["success"] / data["total"] * 100, 1),
            "avg_ms": round(data["ms_sum"] / data["total"]) if data["total"] else 0,
        })

    # جلسات التدريب
    sessions = db.query(TrainingSession).filter(
        TrainingSession.model_id == model_id,
        TrainingSession.created_at >= since
    ).order_by(TrainingSession.created_at.desc()).all()

    return success({
        "model": model.display_name,
        "base_model": model.base_model,
        "period_days": days,
        "total_requests": len(logs),
        "daily_stats": daily_stats,
        "training_sessions": [{
            "id": s.id,
            "type": s.session_type,
            "status": s.status,
            "before_score": s.before_score,
            "after_score": s.after_score,
            "improvement": s.improvement_percent,
            "created_at": s.created_at.isoformat(),
        } for s in sessions],
    })


@router.post("/trigger-check")
def trigger_manual_check(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """إطلاق دورة مراقبة يدوية فوراً — بدون انتظار الجدول التلقائي"""
    from app.core.intelligence.auto_monitor import core_monitor

    background_tasks.add_task(core_monitor._check_all_models)

    return success({
        "message": "✅ بدأت دورة المراقبة اليدوية — Core يفحص كل النماذج الآن",
        "note": "النتائج ستظهر في /monitor/overview خلال دقيقة"
    })


@router.post("/trigger-retrain/{model_id}")
def trigger_model_retrain(
    model_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """إطلاق إعادة تدريب فوري لنموذج محدد"""
    model = db.query(SpecialistModel).filter(
        SpecialistModel.id == model_id
    ).first()
    if not model:
        return success({"error": "النموذج غير موجود"})

    from app.core.intelligence.auto_monitor import core_monitor
    from app.db.session import SessionLocal

    def _run():
        _db = SessionLocal()
        try:
            _model = _db.query(SpecialistModel).filter(
                SpecialistModel.id == model_id
            ).first()
            if _model:
                core_monitor._retrain_with_fresh_data(_model, _db)
        finally:
            _db.close()

    background_tasks.add_task(_run)

    return success({
        "message": f"✅ بدأ Core في إعادة تدريب '{model.display_name}' بأحدث المعلومات",
        "model_id": model_id
    })


@router.get("/tasks")
def list_core_tasks(
    status: str = None,
    task_type: str = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """قائمة بكل مهام Core التلقائية"""
    query = db.query(CoreTask)
    if status:
        query = query.filter(CoreTask.status == status)
    if task_type:
        query = query.filter(CoreTask.task_type == task_type)

    tasks = query.order_by(CoreTask.created_at.desc()).limit(limit).all()

    type_labels = {
        "model_creation":   "إنشاء نموذج",
        "auto_fix":         "إصلاح تلقائي",
        "weekly_retrain":   "تدريب أسبوعي",
        "performance_eval": "تقييم أداء",
    }

    return success([{
        "id": t.id,
        "type": t.task_type,
        "type_label": type_labels.get(t.task_type, t.task_type),
        "status": t.status,
        "model_id": t.target_model_id,
        "output": t.output_data,
        "created_at": t.created_at.isoformat(),
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    } for t in tasks])


@router.get("/weekly-report")
def weekly_report(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """التقرير الأسبوعي الكامل — يعرضه Core في لوحة التحكم"""
    week_ago = datetime.utcnow() - timedelta(days=7)

    models = db.query(SpecialistModel).filter(
        SpecialistModel.status == "active"
    ).all()

    report = {
        "period": {
            "from": week_ago.strftime("%Y-%m-%d"),
            "to": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        "models_summary": [],
        "auto_fixes": 0,
        "weekly_retrains": 0,
        "total_requests": 0,
        "highlights": [],
        "needs_attention": [],
    }

    for m in models:
        logs = db.query(ModelPerformanceLog).filter(
            ModelPerformanceLog.model_id == m.id,
            ModelPerformanceLog.created_at >= week_ago
        ).all()

        if not logs:
            continue

        success_count = sum(1 for l in logs if l.status == "success")
        rate = success_count / len(logs)
        avg_ms = sum(l.response_ms or 0 for l in logs) / len(logs)

        report["total_requests"] += len(logs)

        model_data = {
            "name": m.name,
            "display_name": m.display_name,
            "requests": len(logs),
            "success_rate": round(rate * 100, 1),
            "avg_response_ms": round(avg_ms),
        }
        report["models_summary"].append(model_data)

        if rate >= 0.90:
            report["highlights"].append(
                f"✅ {m.display_name}: أداء ممتاز {rate:.0%} هذا الأسبوع"
            )
        elif rate < 0.70:
            report["needs_attention"].append(m.name)

    # عدد الإصلاحات والتدريبات التلقائية
    auto_fixes = db.query(CoreTask).filter(
        CoreTask.task_type == "auto_fix",
        CoreTask.created_at >= week_ago,
        CoreTask.status == "done"
    ).count()
    weekly_retrains = db.query(CoreTask).filter(
        CoreTask.task_type == "weekly_retrain",
        CoreTask.created_at >= week_ago,
        CoreTask.status == "done"
    ).count()

    report["auto_fixes"] = auto_fixes
    report["weekly_retrains"] = weekly_retrains

    if auto_fixes > 0:
        report["highlights"].append(
            f"🔧 Core أصلح {auto_fixes} نموذج تلقائياً هذا الأسبوع"
        )

    # ترتيب حسب الأداء
    report["models_summary"].sort(key=lambda x: x["success_rate"], reverse=True)

    return success(report)
