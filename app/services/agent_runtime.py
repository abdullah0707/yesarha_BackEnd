"""
Agent runtime.

Each Agent record (app.models.ai.Agent) has:
  - model_id: which AIModel to use
  - agent_type: 'planner' | 'executor' | 'critic' | 'custom'
  - config_json: free-form config, may include:
        { "system_prompt": "...", "temperature": 0.7, ... }

run_agent() calls Ollama with the agent's system prompt + the given
input, and returns the normalized Ollama result (content, tokens, latency).
"""

from sqlalchemy.orm import Session

from app.core.responses import AppError, ErrorCodes
from app.models.ai import Agent, AIModel
from app.services.ollama_client import OllamaClient


DEFAULT_PROMPTS = {
    "planner": (
        "You are the Planner agent of YESARHA Core. "
        "Break the user's request into a clear, numbered, actionable plan. "
        "Be concise and concrete."
    ),
    "executor": (
        "You are the Executor agent of YESARHA Core. "
        "Given a plan or instruction, produce the concrete output/result requested. "
        "Do not explain your reasoning unless asked."
    ),
    "critic": (
        "You are the Critic agent of YESARHA Core. "
        "Evaluate the given result for correctness, quality, and completeness. "
        "Respond with a short verdict and a score from 0 to 1."
    ),
    "custom": "You are a helpful assistant for YESARHA Core."
}


def run_agent(db: Session, agent: Agent, user_input: str, extra_messages: list[dict] | None = None) -> dict:

    model: AIModel | None = None
    if agent.model_id:
        model = db.query(AIModel).filter(AIModel.id == agent.model_id, AIModel.status == "active").first()

    if not model:
        from app.services.model_resolver import resolve_model
        model = resolve_model(db, None)

    config = agent.config_json or {}
    system_prompt = config.get("system_prompt") or DEFAULT_PROMPTS.get(agent.agent_type, DEFAULT_PROMPTS["custom"])
    options = {}
    if "temperature" in config:
        options["temperature"] = config["temperature"]

    messages = [{"role": "system", "content": system_prompt}]
    if extra_messages:
        messages.extend(extra_messages)
    messages.append({"role": "user", "content": user_input})

    client = OllamaClient(base_url=model.endpoint_url)
    result = client.chat(model=model.name, messages=messages, options=options or None)
    result["model_name"] = model.name
    result["model_id"] = model.id
    return result
