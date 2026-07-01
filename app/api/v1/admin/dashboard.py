from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success
from app.services.runtime_config import runtime_cfg
from app.models.specialist import SpecialistModel, ModelPerformanceLog, CoreTask
import requests

router = APIRouter(prefix="/admin/dashboard", tags=["Admin - Dashboard"])


@router.get("/summary")
def dashboard_summary(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    ollama_url = runtime_cfg.get_ollama_url()
    searxng_url = runtime_cfg.get_searxng_url()

    # ── System status ──────────────────────────────────────────────
    ollama_status = "online"
    try:
        resp = requests.get(f"{ollama_url}/api/tags", timeout=2)
        if resp.status_code != 200:
            ollama_status = "offline"
    except Exception:
        ollama_status = "offline"

    # ── Specialist Models stats ────────────────────────────────────
    total_specialists = db.query(SpecialistModel).count()
    active_specialists = db.query(SpecialistModel).filter(
        SpecialistModel.status == "active"
    ).count()
    error_specialists = db.query(SpecialistModel).filter(
        SpecialistModel.status == "error"
    ).count()
    creating_specialists = db.query(SpecialistModel).filter(
        SpecialistModel.status.in_(["creating", "downloading", "training"])
    ).count()

    # صحة النماذج المتخصصة (آخر 24 ساعة)
    day_ago = datetime.utcnow() - timedelta(hours=24)
    specialists_health = []
    active_spec_models = db.query(SpecialistModel).filter(
        SpecialistModel.status == "active"
    ).all()

    for sp in active_spec_models:
        recent_logs = db.query(ModelPerformanceLog).filter(
            ModelPerformanceLog.model_id == sp.id,
            ModelPerformanceLog.created_at >= day_ago
        ).all()

        if recent_logs:
            success_count = sum(1 for l in recent_logs if l.status == "success")
            rate = round((success_count / len(recent_logs)) * 100, 1)
            health = "healthy" if rate >= 85 else ("warning" if rate >= 70 else "critical")
        else:
            rate = None
            health = "no_data"

        specialists_health.append({
            "id": sp.id,
            "name": sp.name,
            "display_name": sp.display_name,
            "specialization": sp.specialization,
            "base_model": sp.base_model,
            "health": health,
            "success_rate_24h": rate,
            "requests_24h": len(recent_logs) if recent_logs else 0,
            "has_api_key": bool(sp.api_key),
        })

    # ── Recent CoreTasks (من Auto Monitor) ────────────────────────
    recent_tasks = db.query(CoreTask).order_by(
        CoreTask.created_at.desc()
    ).limit(5).all()

    task_labels = {
        "model_creation":   "إنشاء نموذج",
        "auto_fix":         "إصلاح تلقائي",
        "weekly_retrain":   "تدريب أسبوعي",
        "performance_eval": "تقييم أداء",
    }

    # ── Total requests stats ───────────────────────────────────────
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total_requests = db.query(ModelPerformanceLog).count()
    today_requests = db.query(ModelPerformanceLog).filter(
        ModelPerformanceLog.created_at >= today_start
    ).count()
    success_requests = db.query(ModelPerformanceLog).filter(
        ModelPerformanceLog.status == "success"
    ).count()
    success_rate = round(
        (success_requests / total_requests * 100), 1
    ) if total_requests > 0 else 0

    return success({
        "system_status": {
            "api":        "online",
            "ollama":     ollama_status,
            "searxng":    _check_searxng(searxng_url),
            "core_model": runtime_cfg.get_core_model(),
        },
        "specialist_models": {
            "total":    total_specialists,
            "active":   active_specialists,
            "creating": creating_specialists,
            "error":    error_specialists,
            "health":   specialists_health,
        },
        "requests": {
            "total":                total_requests,
            "today":                today_requests,
            "success_rate_percent": success_rate,
        },
        "core_activity": [{
            "id":         t.id,
            "type":       t.task_type,
            "type_label": task_labels.get(t.task_type, t.task_type),
            "status":     t.status,
            "model_id":   t.target_model_id,
            "created_at": t.created_at.isoformat(),
        } for t in recent_tasks],
    })


def _check_searxng(url: str) -> str:
    try:
        resp = requests.get(
            f"{url}/search",
            params={"q": "test", "format": "json"},
            headers={"X-Forwarded-For": "127.0.0.1"},
            timeout=2
        )
        return "online" if resp.ok else "offline"
    except Exception:
        return "offline"
