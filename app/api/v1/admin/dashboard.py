from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success
from app.core.config import settings
from app.models.ai import AIModel, Agent
from app.models.operations import Execution, Goal
import requests

router = APIRouter(prefix="/admin/dashboard", tags=["Admin - Dashboard"])


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db), _admin=Depends(get_current_admin)):

    # System status
    ollama_status = "online"
    try:
        resp = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=2)
        if resp.status_code != 200:
            ollama_status = "offline"
    except Exception:
        ollama_status = "offline"

    default_model = db.query(AIModel).filter(AIModel.is_default == True).first()  # noqa: E712

    # Models stats
    total_models = db.query(AIModel).count()
    active_models = db.query(AIModel).filter(AIModel.status == "active").count()

    # Agents stats
    total_agents = db.query(Agent).count()
    active_agents = db.query(Agent).filter(Agent.status == "active").count()

    # Executions stats
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total_executions = db.query(Execution).count()
    today_executions = db.query(Execution).filter(Execution.created_at >= today_start).count()
    success_executions = db.query(Execution).filter(Execution.status == "success").count()

    success_rate = round((success_executions / total_executions * 100), 1) if total_executions > 0 else 0

    # Recent executions
    recent_executions = db.query(Execution).order_by(
        Execution.created_at.desc()
    ).limit(10).all()

    # Recent goals
    recent_goals = db.query(Goal).order_by(
        Goal.created_at.desc()
    ).limit(5).all()

    return success({
        "system_status": {
            "api": "online",
            "ollama": ollama_status,
            "default_model": default_model.name if default_model else None
        },
        "models": {
            "total": total_models,
            "active": active_models,
            "inactive": total_models - active_models
        },
        "agents": {
            "total": total_agents,
            "active": active_agents,
            "inactive": total_agents - active_agents
        },
        "executions": {
            "total": total_executions,
            "today": today_executions,
            "success_rate_percent": success_rate
        },
        "recent_executions": [{
            "id": e.id,
            "intent": e.intent,
            "tool": e.tool,
            "status": e.status,
            "created_at": e.created_at.isoformat()
        } for e in recent_executions],
        "recent_goals": [{
            "id": g.id,
            "title": g.title,
            "status": g.status,
            "created_at": g.created_at.isoformat()
        } for g in recent_goals]
    })
