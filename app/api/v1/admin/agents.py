from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from decimal import Decimal

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.ai import Agent, AgentEvaluation
from app.models.operations import Execution
from app.schemas.ai import AgentCreate, AgentUpdate, AgentOut

router = APIRouter(prefix="/admin/agents", tags=["Admin - Agents"])

VALID_AGENT_TYPES = ("planner", "executor", "critic", "custom")
VALID_STATUS = ("active", "inactive")


@router.get("")
def list_agents(
    params: ListParams = Depends(),
    agent_type: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    query = db.query(Agent)
    if agent_type:
        query = query.filter(Agent.agent_type == agent_type)
    if status:
        query = query.filter(Agent.status == status)
    if params.search:
        query = query.filter(Agent.name.ilike(f"%{params.search}%"))

    query = apply_sort(query, Agent, params.sort, default_field="id")
    items, total = apply_pagination(query, params)

    return paginated(
        [AgentOut.model_validate(i).model_dump() for i in items],
        params.page, params.limit, total
    )


@router.post("")
def create_agent(
    payload: AgentCreate,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    if payload.agent_type not in VALID_AGENT_TYPES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"agent_type must be one of {VALID_AGENT_TYPES}")
    if payload.status not in VALID_STATUS:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"status must be one of {VALID_STATUS}")

    agent = Agent(**payload.model_dump())
    db.add(agent)
    db.commit()
    db.refresh(agent)

    return success(AgentOut.model_validate(agent).model_dump())


@router.get("/{agent_id}")
def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise AppError(ErrorCodes.NOT_FOUND, "Agent not found", 404)
    return success(AgentOut.model_validate(agent).model_dump())


@router.patch("/{agent_id}")
def update_agent(
    agent_id: int,
    payload: AgentUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise AppError(ErrorCodes.NOT_FOUND, "Agent not found", 404)

    data = payload.model_dump(exclude_unset=True)
    if "agent_type" in data and data["agent_type"] not in VALID_AGENT_TYPES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"agent_type must be one of {VALID_AGENT_TYPES}")
    if "status" in data and data["status"] not in VALID_STATUS:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"status must be one of {VALID_STATUS}")

    for key, value in data.items():
        setattr(agent, key, value)

    db.commit()
    db.refresh(agent)
    return success(AgentOut.model_validate(agent).model_dump())


@router.delete("/{agent_id}")
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise AppError(ErrorCodes.NOT_FOUND, "Agent not found", 404)
    db.delete(agent)
    db.commit()
    return success({"deleted": True, "id": agent_id})


@router.get("/{agent_id}/performance")
def get_agent_performance(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise AppError(ErrorCodes.NOT_FOUND, "Agent not found", 404)

    evaluations = db.query(AgentEvaluation).filter(
        AgentEvaluation.agent_id == agent_id
    ).order_by(AgentEvaluation.created_at.desc()).limit(50).all()

    avg_score = None
    if evaluations:
        avg_score = float(
            sum(Decimal(str(e.score)) for e in evaluations) / len(evaluations)
        )

    return success({
        "agent_id": agent_id,
        "agent_name": agent.name,
        "average_score": avg_score,
        "evaluations_count": len(evaluations),
        "recent_evaluations": [{
            "id": e.id,
            "score": float(e.score),
            "metrics": e.metrics_json,
            "created_at": e.created_at.isoformat()
        } for e in evaluations]
    })


@router.get("/{agent_id}/logs")
def get_agent_logs(
    agent_id: int,
    params: ListParams = Depends(),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise AppError(ErrorCodes.NOT_FOUND, "Agent not found", 404)

    # logs are stored as Executions with tool_input containing agent_id
    query = db.query(Execution).filter(
        Execution.tool_input["agent_id"].as_integer() == agent_id
    )
    query = apply_sort(query, Execution, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    data = [{
        "id": e.id,
        "intent": e.intent,
        "tool": e.tool,
        "status": e.status,
        "result": e.result,
        "created_at": e.created_at.isoformat()
    } for e in items]

    return paginated(data, params.page, params.limit, total)
