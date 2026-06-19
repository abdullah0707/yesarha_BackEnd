from pydantic import BaseModel, Field
from typing import Optional


class RunRequest(BaseModel):
    goal: str = Field(min_length=1)
    use_planner: bool = True
    use_critic: bool = True
    service_key: Optional[str] = "full_plan"


class RunStepResult(BaseModel):
    agent_type: str
    agent_id: Optional[int] = None
    model: str
    content: str
    tokens_input: int
    tokens_output: int
    latency_ms: int
    credits_charged: float
