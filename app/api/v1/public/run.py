from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from decimal import Decimal

from app.db.session import get_db
from app.core.deps import get_current_user
from app.core.responses import success, AppError, ErrorCodes
from app.models.user import User
from app.models.ai import Agent
from app.models.operations import Goal, Execution
from app.schemas.run import RunRequest
from app.services import credit_engine
from app.services.agent_runtime import run_agent

router = APIRouter(prefix="/run", tags=["Run"])


def _get_active_agent(db: Session, agent_type: str) -> Agent | None:
    return db.query(Agent).filter(Agent.agent_type == agent_type, Agent.status == "active").first()


def _execute_step(db: Session, user, wallet, policy, agent: Agent, user_input: str, service_key: str, extra_messages=None):
    pricing = credit_engine.resolve_pricing(db, service_key, model_id=agent.model_id, agent_id=agent.id)

    estimated = credit_engine.estimate_cost(pricing, estimated_tokens=0, estimated_seconds=0)
    credit_engine.ensure_sufficient_balance(wallet, estimated)

    result = run_agent(db, agent, user_input, extra_messages=extra_messages)

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
            related_model=result["model_name"],
            related_agent=agent.name
        )

    credit_engine.log_usage(
        db, user.id, service_key, result["model_name"], agent.id,
        tokens_input=result["tokens_input"],
        tokens_output=result["tokens_output"],
        latency_ms=result["latency_ms"],
        credits_charged=actual_cost,
        calculation_type=pricing.calculation_type if pricing else None,
        result_status="success"
    )

    return result, actual_cost


@router.post("")
def run_pipeline(payload: RunRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):

    service_key = payload.service_key or "full_plan"

    policy = credit_engine.get_policy(db)
    credit_engine.check_limits(db, user.id, policy)
    wallet = credit_engine.get_or_create_wallet(db, user.id)

    steps = []
    plan_text = payload.goal

    # store the goal
    goal_record = Goal(user_id=user.id, title=payload.goal, status="active")
    db.add(goal_record)
    db.commit()
    db.refresh(goal_record)

    try:
        # -------------------------------------------------
        # 1. PLANNER (optional)
        # -------------------------------------------------
        if payload.use_planner:
            planner = _get_active_agent(db, "planner")
            if planner:
                result, cost = _execute_step(db, user, wallet, policy, planner, payload.goal, service_key)
                plan_text = result["content"]
                steps.append({
                    "agent_type": "planner",
                    "agent_id": planner.id,
                    "model": result["model_name"],
                    "content": result["content"],
                    "tokens_input": result["tokens_input"],
                    "tokens_output": result["tokens_output"],
                    "latency_ms": result["latency_ms"],
                    "credits_charged": float(cost)
                })

        # -------------------------------------------------
        # 2. EXECUTOR (required)
        # -------------------------------------------------
        executor = _get_active_agent(db, "executor")
        if not executor:
            raise AppError(ErrorCodes.NOT_FOUND, "No active 'executor' agent configured", 404)

        executor_input = plan_text if payload.use_planner else payload.goal
        result, cost = _execute_step(db, user, wallet, policy, executor, executor_input, service_key)
        final_output = result["content"]

        steps.append({
            "agent_type": "executor",
            "agent_id": executor.id,
            "model": result["model_name"],
            "content": result["content"],
            "tokens_input": result["tokens_input"],
            "tokens_output": result["tokens_output"],
            "latency_ms": result["latency_ms"],
            "credits_charged": float(cost)
        })

        # -------------------------------------------------
        # 3. CRITIC (optional)
        # -------------------------------------------------
        critic_eval = None
        if payload.use_critic:
            critic = _get_active_agent(db, "critic")
            if critic:
                critic_input = f"Goal:\n{payload.goal}\n\nResult to evaluate:\n{final_output}"
                result, cost = _execute_step(db, user, wallet, policy, critic, critic_input, service_key)

                steps.append({
                    "agent_type": "critic",
                    "agent_id": critic.id,
                    "model": result["model_name"],
                    "content": result["content"],
                    "tokens_input": result["tokens_input"],
                    "tokens_output": result["tokens_output"],
                    "latency_ms": result["latency_ms"],
                    "credits_charged": float(cost)
                })
                critic_eval = result["content"]

    except AppError as e:
        db.add(Execution(
            user_id=user.id, intent=service_key, tool="agent_pipeline",
            tool_input={"goal": payload.goal}, status="failed",
            result={"error": e.message, "steps": steps}
        ))
        db.commit()
        raise

    total_cost = sum(s["credits_charged"] for s in steps)

    db.add(Execution(
        user_id=user.id, intent=service_key, tool="agent_pipeline",
        tool_input={"goal": payload.goal},
        status="success",
        result={"steps_count": len(steps), "total_credits": total_cost}
    ))
    db.commit()
    db.refresh(wallet)

    return success({
        "goal_id": goal_record.id,
        "final_output": final_output,
        "critic_evaluation": critic_eval,
        "steps": steps,
        "total_credits_charged": total_cost,
        "wallet": {
            "subscription_credits": wallet.subscription_credits,
            "topup_credits": wallet.topup_credits,
            "total_credits": wallet.total_credits
        }
    })
