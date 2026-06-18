"""
Internal test tools for admins.
No credit deduction — pure model testing.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, AppError, ErrorCodes
from app.models.user import Admin
from app.models.ai import Agent
from app.models.operations import Execution
from app.services.model_resolver import resolve_model
from app.services.ollama_client import OllamaClient
from app.services.agent_runtime import run_agent
from pydantic import BaseModel, Field


class TestChatRequest(BaseModel):
    message: str = Field(min_length=1)
    model_id: Optional[int] = None
    agent_id: Optional[int] = None
    system_prompt: Optional[str] = None
    history: Optional[list[dict]] = None


class TestRunRequest(BaseModel):
    goal: str = Field(min_length=1)
    use_planner: bool = True
    use_critic: bool = True


router = APIRouter(prefix="/admin/test", tags=["Admin - Test Tools"])


@router.post("/chat")
def test_chat(
    payload: TestChatRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
    # resolve agent if given
    agent: Agent | None = None
    if payload.agent_id:
        agent = db.query(Agent).filter(
            Agent.id == payload.agent_id,
            Agent.status == "active"
        ).first()
        if not agent:
            raise AppError(ErrorCodes.NOT_FOUND, "Agent not found or inactive", 404)

    # resolve model
    ai_model = None
    if agent and agent.model_id and not payload.model_id:
        from app.models.ai import AIModel
        ai_model = db.query(AIModel).filter(
            AIModel.id == agent.model_id,
            AIModel.status == "active"
        ).first()

    if not ai_model:
        from app.models.ai import AIModel
        if payload.model_id:
            ai_model = db.query(AIModel).filter(
                AIModel.id == payload.model_id,
                AIModel.status == "active"
            ).first()
            if not ai_model:
                raise AppError(ErrorCodes.MODEL_UNAVAILABLE, "Model not found or inactive", 404)
        else:
            ai_model = resolve_model(db, None)

    # build messages
    messages = []
    if payload.system_prompt:
        messages.append({"role": "system", "content": payload.system_prompt})
    elif agent:
        config = agent.config_json or {}
        if config.get("system_prompt"):
            messages.append({"role": "system", "content": config["system_prompt"]})

    if payload.history:
        messages.extend(payload.history)

    messages.append({"role": "user", "content": payload.message})

    # call ollama — no credit deduction
    client = OllamaClient(base_url=ai_model.endpoint_url)
    result = client.chat(model=ai_model.name, messages=messages)

    # log execution (for analytics — no financial data)
    db.add(Execution(
        user_id=admin.id,
        intent="admin_test_chat",
        tool="ollama_chat",
        tool_input={"model": ai_model.name, "agent_id": agent.id if agent else None},
        status="success",
        result={
            "tokens_input": result["tokens_input"],
            "tokens_output": result["tokens_output"],
            "latency_ms": result["latency_ms"]
        }
    ))
    db.commit()

    return success({
        "content": result["content"],
        "model": ai_model.name,
        "agent": agent.name if agent else None,
        "tokens_input": result["tokens_input"],
        "tokens_output": result["tokens_output"],
        "latency_ms": result["latency_ms"]
    })


@router.post("/run")
def test_run(
    payload: TestRunRequest,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
    from app.models.ai import Agent
    from app.models.operations import Goal

    def _get_agent(agent_type: str) -> Agent | None:
        return db.query(Agent).filter(
            Agent.agent_type == agent_type,
            Agent.status == "active"
        ).first()

    steps = []
    plan_text = payload.goal

    # save goal for analytics
    goal_record = Goal(user_id=admin.id, title=payload.goal, status="active")
    db.add(goal_record)
    db.commit()
    db.refresh(goal_record)

    try:
        # 1. PLANNER
        if payload.use_planner:
            planner = _get_agent("planner")
            if planner:
                result = run_agent(db, planner, payload.goal)
                plan_text = result["content"]
                steps.append({
                    "agent_type": "planner",
                    "agent_id": planner.id,
                    "agent_name": planner.name,
                    "model": result["model_name"],
                    "content": result["content"],
                    "tokens_input": result["tokens_input"],
                    "tokens_output": result["tokens_output"],
                    "latency_ms": result["latency_ms"]
                })

        # 2. EXECUTOR
        executor = _get_agent("executor")
        if not executor:
            raise AppError(ErrorCodes.NOT_FOUND, "No active 'executor' agent configured", 404)

        executor_input = plan_text if payload.use_planner else payload.goal
        result = run_agent(db, executor, executor_input)
        final_output = result["content"]

        steps.append({
            "agent_type": "executor",
            "agent_id": executor.id,
            "agent_name": executor.name,
            "model": result["model_name"],
            "content": result["content"],
            "tokens_input": result["tokens_input"],
            "tokens_output": result["tokens_output"],
            "latency_ms": result["latency_ms"]
        })

        # 3. CRITIC
        critic_eval = None
        if payload.use_critic:
            critic = _get_agent("critic")
            if critic:
                critic_input = f"Goal:\n{payload.goal}\n\nResult to evaluate:\n{final_output}"
                result = run_agent(db, critic, critic_input)
                critic_eval = result["content"]
                steps.append({
                    "agent_type": "critic",
                    "agent_id": critic.id,
                    "agent_name": critic.name,
                    "model": result["model_name"],
                    "content": result["content"],
                    "tokens_input": result["tokens_input"],
                    "tokens_output": result["tokens_output"],
                    "latency_ms": result["latency_ms"]
                })

    except AppError as e:
        db.add(Execution(
            user_id=admin.id, intent="admin_test_run",
            tool="agent_pipeline", tool_input={"goal": payload.goal},
            status="failed", result={"error": e.message, "steps_completed": len(steps)}
        ))
        db.commit()
        raise

    total_latency = sum(s["latency_ms"] for s in steps)
    total_tokens = sum(s["tokens_output"] for s in steps)

    db.add(Execution(
        user_id=admin.id, intent="admin_test_run",
        tool="agent_pipeline", tool_input={"goal": payload.goal},
        status="success",
        result={"steps_count": len(steps), "total_latency_ms": total_latency}
    ))
    db.commit()

    return success({
        "goal_id": goal_record.id,
        "final_output": final_output,
        "critic_evaluation": critic_eval,
        "steps": steps,
        "summary": {
            "total_steps": len(steps),
            "total_latency_ms": total_latency,
            "total_tokens_output": total_tokens
        }
    })
