from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.operations import Execution, Goal
from app.models.ai import AIModel, Agent

router = APIRouter(prefix="/admin/analytics", tags=["Admin - Analytics"])


@router.get("/tool-performance")
def tool_performance(
    days: int = 30,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    since = datetime.utcnow() - timedelta(days=days)

    rows = db.query(
        Execution.tool,
        Execution.status,
        func.count(Execution.id).label("count")
    ).filter(
        Execution.created_at >= since
    ).group_by(Execution.tool, Execution.status).all()

    # build per-tool summary
    tools: dict = {}
    for row in rows:
        tool = row.tool or "unknown"
        if tool not in tools:
            tools[tool] = {"tool": tool, "success": 0, "failed": 0, "total": 0}
        tools[tool][row.status] = tools[tool].get(row.status, 0) + row.count
        tools[tool]["total"] += row.count

    for t in tools.values():
        t["success_rate"] = round(
            t["success"] / t["total"] * 100, 1
        ) if t["total"] > 0 else 0

    return success(list(tools.values()))


@router.get("/intent-distribution")
def intent_distribution(
    days: int = 30,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    since = datetime.utcnow() - timedelta(days=days)

    rows = db.query(
        Execution.intent,
        func.count(Execution.id).label("count")
    ).filter(
        Execution.created_at >= since,
        Execution.intent.isnot(None)
    ).group_by(Execution.intent).order_by(desc("count")).all()

    total = sum(r.count for r in rows)
    data = [{
        "intent": r.intent,
        "count": r.count,
        "percent": round(r.count / total * 100, 1) if total > 0 else 0
    } for r in rows]

    return success(data)


@router.get("/failures")
def failures(
    params: ListParams = Depends(),
    days: int = 30,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    since = datetime.utcnow() - timedelta(days=days)

    query = db.query(Execution).filter(
        Execution.status == "failed",
        Execution.created_at >= since
    )
    query = apply_sort(query, Execution, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    data = [{
        "id": e.id,
        "intent": e.intent,
        "tool": e.tool,
        "tool_input": e.tool_input,
        "result": e.result,
        "created_at": e.created_at.isoformat()
    } for e in items]

    return paginated(data, params.page, params.limit, total)


@router.get("/insights")
def insights(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    since_7d = datetime.utcnow() - timedelta(days=7)
    since_30d = datetime.utcnow() - timedelta(days=30)

    total = db.query(Execution).filter(Execution.created_at >= since_30d).count()
    failed = db.query(Execution).filter(
        Execution.created_at >= since_30d,
        Execution.status == "failed"
    ).count()

    active_models = db.query(AIModel).filter(AIModel.status == "active").count()
    active_agents = db.query(Agent).filter(Agent.status == "active").count()

    recent_executions = db.query(Execution).filter(
        Execution.created_at >= since_7d
    ).count()

    insights_list = []

    if total > 0:
        fail_rate = failed / total * 100
        if fail_rate > 20:
            insights_list.append({
                "type": "warning",
                "title": {"ar": "معدل فشل مرتفع", "en": "High Failure Rate"},
                "message": {"ar": f"معدل الفشل {fail_rate:.1f}% خلال آخر 30 يوم — راجع سجلات الأخطاء",
                            "en": f"Failure rate is {fail_rate:.1f}% over the last 30 days — check error logs"}
            })

    if active_models == 0:
        insights_list.append({
            "type": "error",
            "title": {"ar": "لا يوجد نموذج نشط", "en": "No Active Model"},
            "message": {"ar": "لا يوجد أي نموذج ذكاء اصطناعي نشط — قم بتفعيل نموذج أولاً",
                        "en": "No active AI model found — please activate a model first"}
        })

    if active_agents < 3:
        insights_list.append({
            "type": "info",
            "title": {"ar": "وكلاء غير مكتملة", "en": "Incomplete Agents"},
            "message": {"ar": f"يوجد {active_agents} وكيل نشط فقط — يُنصح بتفعيل Planner+Executor+Critic",
                        "en": f"Only {active_agents} active agent(s) — recommended: Planner+Executor+Critic"}
        })

    if recent_executions > 0 and not insights_list:
        insights_list.append({
            "type": "success",
            "title": {"ar": "النظام يعمل بشكل جيد", "en": "System Running Well"},
            "message": {"ar": f"{recent_executions} تنفيذ خلال آخر 7 أيام دون مشاكل",
                        "en": f"{recent_executions} executions in the last 7 days without issues"}
        })

    return success({
        "insights": insights_list,
        "stats_30d": {
            "total_executions": total,
            "failed_executions": failed,
            "failure_rate_percent": round(failed / total * 100, 1) if total > 0 else 0,
            "active_models": active_models,
            "active_agents": active_agents
        }
    })


@router.get("/goals")
def list_goals(
    params: ListParams = Depends(),
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    query = db.query(Goal)
    if status:
        query = query.filter(Goal.status == status)

    query = apply_sort(query, Goal, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    data = [{
        "id": g.id,
        "title": g.title,
        "status": g.status,
        "priority": g.priority,
        "created_at": g.created_at.isoformat()
    } for g in items]

    return paginated(data, params.page, params.limit, total)
