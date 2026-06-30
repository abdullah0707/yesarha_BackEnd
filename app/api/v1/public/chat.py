from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from decimal import Decimal

from app.db.session import get_db
from app.core.deps import get_current_user
from app.core.responses import success, AppError, ErrorCodes
from app.models.user import User
from app.models.ai import Agent
from app.schemas.chat import ChatRequest
from app.services import credit_engine
from app.services.model_resolver import resolve_model
from app.services.ollama_client import OllamaClient

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("")
def chat(payload: ChatRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):

    service_key = payload.service_key or "chat_message"

    # ------------------------------------------------------------
    # 1. Resolve agent (optional) and model
    # ------------------------------------------------------------
    agent: Agent | None = None
    if payload.agent_id:
        agent = db.query(Agent).filter(Agent.id == payload.agent_id, Agent.status == "active").first()
        if not agent:
            raise AppError(ErrorCodes.NOT_FOUND, "Agent not found or inactive", 404)

    model_name = payload.model or (agent.model_id if agent else None)
    # if agent.model_id is an int (FK), resolve by id via registry; otherwise treat as name
    ai_model = None
    if agent and agent.model_id and not payload.model:
        from app.models.ai import AIModel
        ai_model = db.query(AIModel).filter(AIModel.id == agent.model_id, AIModel.status == "active").first()

    if not ai_model:
        ai_model = resolve_model(db, payload.model)

    # ------------------------------------------------------------
    # 2. Resolve pricing + credit policy
    # ------------------------------------------------------------
    policy = credit_engine.get_policy(db)
    credit_engine.check_limits(db, user.id, policy)

    pricing = credit_engine.resolve_pricing(
        db, service_key, model_id=ai_model.id, agent_id=agent.id if agent else None
    )

    wallet = credit_engine.get_or_create_wallet(db, user.id)

    # pre-check with a rough estimate (for fixed pricing this is exact)
    estimated = credit_engine.estimate_cost(pricing, estimated_tokens=0, estimated_seconds=0)
    credit_engine.ensure_sufficient_balance(wallet, estimated)

    # ------------------------------------------------------------
    # 3. Build messages and call Ollama
    # ------------------------------------------------------------
    messages = []
    if payload.system_prompt:
        messages.append({"role": "system", "content": payload.system_prompt})

    if payload.history:
        messages.extend([{"role": m.role, "content": m.content} for m in payload.history])

    messages.append({"role": "user", "content": payload.message})

    client = OllamaClient(base_url=ai_model.endpoint_url)

    result_status = "success"
    try:
        result = client.chat(model=ai_model.name, messages=messages)
    except AppError as e:
        # Log a failed execution + usage entry with zero cost, then re-raise
        credit_engine.log_usage(
            db, user.id, service_key, ai_model.name,
            agent.id if agent else None,
            tokens_input=0, tokens_output=0, latency_ms=0,
            credits_charged=Decimal("0"),
            calculation_type=pricing.calculation_type if pricing else None,
            result_status="failed"
        )
        db.commit()
        raise

    # ------------------------------------------------------------
    # 4. Compute actual cost + deduct + log
    # ------------------------------------------------------------
    actual_cost = credit_engine.compute_actual_cost(
        pricing,
        tokens_input=result["tokens_input"],
        tokens_output=result["tokens_output"],
        latency_ms=result["latency_ms"]
    )

    if actual_cost > 0:
        credit_engine.deduct_credits(
            db, wallet, actual_cost, policy,
            related_service=service_key,
            related_model=ai_model.name,
            related_agent=agent.name if agent else None
        )

    credit_engine.log_usage(
        db, user.id, service_key, ai_model.name,
        agent.id if agent else None,
        tokens_input=result["tokens_input"],
        tokens_output=result["tokens_output"],
        latency_ms=result["latency_ms"],
        credits_charged=actual_cost,
        calculation_type=pricing.calculation_type if pricing else None,
        result_status=result_status
    )

    db.commit()
    db.refresh(wallet)

    return success({
        "content": result["content"],
        "model": ai_model.name,
        "tokens_input": result["tokens_input"],
        "tokens_output": result["tokens_output"],
        "latency_ms": result["latency_ms"],
        "credits_charged": float(actual_cost),
        "wallet": {
            "subscription_credits": wallet.subscription_credits,
            "topup_credits": wallet.topup_credits,
            "total_credits": wallet.total_credits
        }
    })
