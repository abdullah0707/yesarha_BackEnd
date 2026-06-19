from pydantic import BaseModel, Field
from typing import Optional


class ChatMessage(BaseModel):
    role: str  # 'user' | 'assistant' | 'system'
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    model: Optional[str] = None          # model name override (Model Registry)
    agent_id: Optional[int] = None        # which agent (pricing/behavior) is used
    service_key: Optional[str] = "chat_message"
    history: Optional[list[ChatMessage]] = None
    system_prompt: Optional[str] = None


class ChatResponse(BaseModel):
    content: str
    model: str
    tokens_input: int
    tokens_output: int
    latency_ms: int
    credits_charged: float
    wallet: dict
