from pydantic import BaseModel
from typing import Optional
from decimal import Decimal


class AIModelBase(BaseModel):
    name: str
    version: Optional[str] = None
    type: str = "general"  # reasoning | planning | general | vision
    status: str = "active"  # active | inactive
    is_default: bool = False
    cost_per_call: Optional[Decimal] = None
    endpoint_url: Optional[str] = None


class AIModelCreate(AIModelBase):
    pass


class AIModelUpdate(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    is_default: Optional[bool] = None
    cost_per_call: Optional[Decimal] = None
    endpoint_url: Optional[str] = None


class AIModelOut(AIModelBase):
    id: int

    class Config:
        from_attributes = True


class AgentBase(BaseModel):
    name: str
    model_id: Optional[int] = None
    agent_type: str = "custom"  # planner | executor | critic | custom
    config_json: Optional[dict] = None
    status: str = "active"


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    model_id: Optional[int] = None
    agent_type: Optional[str] = None
    config_json: Optional[dict] = None
    status: Optional[str] = None


class AgentOut(AgentBase):
    id: int

    class Config:
        from_attributes = True
