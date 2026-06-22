from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Numeric
from datetime import datetime

from app.db.session import Base


class AIModel(Base):
    __tablename__ = "ai_models"

    id = Column(Integer, primary_key=True, autoincrement=True)

    name = Column(String, nullable=False)        # e.g. 'qwen3:8b'
    version = Column(String, nullable=True)
    type = Column(String, default="general")     # 'reasoning' | 'planning' | 'general' | 'vision'
    status = Column(String, default="active")    # 'active' | 'inactive'
    is_default = Column(Boolean, default=False)

    cost_per_call = Column(Numeric(12, 4), nullable=True)
    endpoint_url = Column(String, nullable=True)  # ollama base url for this model
    description = Column(String, nullable=True)
<<<<<<< HEAD

=======
    
>>>>>>> 6a47c5538944ce69ebc8ed72d0e3e2d583fe4e4d
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)

    name = Column(String, nullable=False)
    model_id = Column(Integer, ForeignKey("ai_models.id"), nullable=True)
    agent_type = Column(String, default="custom")  # 'planner' | 'executor' | 'critic' | 'custom'
    config_json = Column(JSON, default=dict)
    status = Column(String, default="active")       # 'active' | 'inactive'

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentEvaluation(Base):
    __tablename__ = "agent_evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False, index=True)

    score = Column(Numeric(5, 4), nullable=False)
    metrics_json = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
