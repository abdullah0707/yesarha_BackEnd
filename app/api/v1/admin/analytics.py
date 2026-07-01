from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.specialist import SpecialistModel, ModelPerformanceLog

router = APIRouter(prefix="/admin/analytics", tags=["Admin - Analytics"])


@router.get("/specialist-performance")
def specialist_performance(
    days: int = 30,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """أداء النماذج المتخصصة — نجاح/فشل/متوسط زمن الاستجابة"""
    since = datetime.utcnow() - timedelta(days=days)

    rows = db.query(
        ModelPerformanceLog.model_id,
        ModelPerformanceLog.status,
        func.count(ModelPerformanceLog.id).label("count"),
        func.avg(ModelPerformanceLog.response_ms).label("avg_ms"),
    ).filter(
        ModelPerformanceLog.created_at >= since
    ).group_by(ModelPerformanceLog.model_id, ModelPerformanceLog.status).all()

    # aggregate per model
    models: dict = {}
    for row in rows:
        mid = row.model_id
        if mid not in models:
            models[mid] = {"model_id": mid, "success": 0, "failed": 0, "total": 0, "avg_ms": 0}
        models[mid][row.status] = models[mid].get(row.status, 0) + row.count
        models[mid]["total"] += row.count
        if row.status == "success" and row.avg_ms:
            models[mid]["avg_ms"] = round(float(row.avg_ms), 1)

    # enrich with model names
    all_ids = list(models.keys())
    spec_map = {
        s.id: s for s in db.query(SpecialistModel).filter(SpecialistModel.id.in_(all_ids)).all()
    } if all_ids else {}

    result = []
    for mid, data in models.items():
        spec = spec_map.get(mid)
        data["success_rate"] = round(data["success"] / data["total"] * 100, 1) if data["total"] > 0 else 0
        data["name"] = spec.name if spec else f"model_{mid}"
        data["display_name"] = spec.display_name if spec else f"Model {mid}"
        data["specialization"] = spec.specialization if spec else "unknown"
        result.append(data)

    result.sort(key=lambda x: x["total"], reverse=True)
    return success(result)


@router.get("/request-distribution")
def request_distribution(
    days: int = 30,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """توزيع الطلبات حسب التخصص"""
    since = datetime.utcnow() - timedelta(days=days)

    rows = db.query(
        ModelPerformanceLog.model_id,
        func.count(ModelPerformanceLog.id).label("count")
    ).filter(
        ModelPerformanceLog.created_at >= since
    ).group_by(ModelPerformanceLog.model_id).order_by(desc("count")).all()

    total = sum(r.count for r in rows)
    all_ids = [r.model_id for r in rows]
    spec_map = {
        s.id: s for s in db.query(SpecialistModel).filter(SpecialistModel.id.in_(all_ids)).all()
    } if all_ids else {}

    data = [{
        "specialization": spec_map[r.model_id].specialization if r.model_id in spec_map else "unknown",
        "display_name":   spec_map[r.model_id].display_name   if r.model_id in spec_map else f"Model {r.model_id}",
        "count":   r.count,
        "percent": round(r.count / total * 100, 1) if total > 0 else 0,
    } for r in rows]

    return success(data)


@router.get("/failures")
def failures(
    params: ListParams = Depends(),
    days: int = 30,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """سجل الطلبات الفاشلة للنماذج المتخصصة"""
    since = datetime.utcnow() - timedelta(days=days)

    query = db.query(ModelPerformanceLog).filter(
        ModelPerformanceLog.status == "failed",
        ModelPerformanceLog.created_at >= since
    )
    query = apply_sort(query, ModelPerformanceLog, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    all_ids = list({item.model_id for item in items})
    spec_map = {
        s.id: s for s in db.query(SpecialistModel).filter(SpecialistModel.id.in_(all_ids)).all()
    } if all_ids else {}

    data = [{
        "id":           e.id,
        "model_id":     e.model_id,
        "model_name":   spec_map[e.model_id].name if e.model_id in spec_map else f"model_{e.model_id}",
        "prompt_tokens": e.tokens_input,
        "response_ms":  e.response_ms,
        "error":        e.error_message,
        "created_at":   e.created_at.isoformat(),
    } for e in items]

    return paginated(data, params.page, params.limit, total)


@router.get("/insights")
def insights(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """تحليلات واقتراحات بناءً على أداء النماذج"""
    since_7d  = datetime.utcnow() - timedelta(days=7)
    since_30d = datetime.utcnow() - timedelta(days=30)

    total_30d  = db.query(ModelPerformanceLog).filter(ModelPerformanceLog.created_at >= since_30d).count()
    failed_30d = db.query(ModelPerformanceLog).filter(
        ModelPerformanceLog.created_at >= since_30d,
        ModelPerformanceLog.status == "failed"
    ).count()

    active_specialists = db.query(SpecialistModel).filter(SpecialistModel.status == "active").count()
    total_specialists  = db.query(SpecialistModel).count()
    recent_7d = db.query(ModelPerformanceLog).filter(ModelPerformanceLog.created_at >= since_7d).count()

    insights_list = []

    if total_30d > 0:
        fail_rate = failed_30d / total_30d * 100
        if fail_rate > 20:
            insights_list.append({
                "type": "warning",
                "title":   {"ar": "معدل فشل مرتفع",      "en": "High Failure Rate"},
                "message": {"ar": f"معدل الفشل {fail_rate:.1f}% خلال آخر 30 يوم",
                            "en": f"Failure rate is {fail_rate:.1f}% over the last 30 days"},
            })

    if active_specialists == 0:
        insights_list.append({
            "type": "error",
            "title":   {"ar": "لا يوجد نموذج متخصص نشط", "en": "No Active Specialist"},
            "message": {"ar": "أنشئ نموذجاً متخصصاً وفعّله من لوحة التحكم",
                        "en": "Create a specialist model and activate it from the dashboard"},
        })
    elif active_specialists < total_specialists:
        inactive = total_specialists - active_specialists
        insights_list.append({
            "type": "info",
            "title":   {"ar": "نماذج غير نشطة",    "en": "Inactive Specialists"},
            "message": {"ar": f"{inactive} نموذج غير نشط — راجع حالتها",
                        "en": f"{inactive} specialist(s) are not active — check their status"},
        })

    if recent_7d > 0 and not insights_list:
        insights_list.append({
            "type": "success",
            "title":   {"ar": "النظام يعمل بشكل جيد", "en": "System Running Well"},
            "message": {"ar": f"{recent_7d} طلب خلال آخر 7 أيام",
                        "en": f"{recent_7d} requests in the last 7 days"},
        })

    return success({
        "insights": insights_list,
        "stats_30d": {
            "total_requests":       total_30d,
            "failed_requests":      failed_30d,
            "failure_rate_percent": round(failed_30d / total_30d * 100, 1) if total_30d > 0 else 0,
            "active_specialists":   active_specialists,
            "total_specialists":    total_specialists,
        },
    })
