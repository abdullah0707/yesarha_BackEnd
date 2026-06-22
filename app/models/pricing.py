from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric, JSON
from datetime import datetime

from app.db.session import Base


class ServicePricing(Base):
    __tablename__ = "service_pricing"

    id = Column(Integer, primary_key=True, autoincrement=True)

    service_key = Column(String, nullable=False, index=True)  # e.g. 'chat_message'
    model_id = Column(Integer, ForeignKey("ai_models.id"), nullable=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)

    calculation_type = Column(String, nullable=False, default="fixed")
    # 'fixed' | 'per_token' | 'per_second'

    credits_cost = Column(Numeric(12, 4), nullable=True)     # used for 'fixed'
    token_rate = Column(Numeric(12, 4), nullable=True)       # credits per `tokens_per_unit`
    tokens_per_unit = Column(Integer, nullable=True)         # e.g. 100 tokens
    second_rate = Column(Numeric(12, 4), nullable=True)      # credits per second

    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreditPolicy(Base):
    """
    Singleton-style table: a single row holds the global credit policy.
    """
    __tablename__ = "credit_policy"

    id = Column(Integer, primary_key=True, autoincrement=True)

    deduction_priority = Column(String, default="topup_first")
    # 'topup_first' | 'subscription_first'

    daily_limit = Column(Integer, nullable=True)
    monthly_limit = Column(Integer, nullable=True)
    low_balance_threshold = Column(Integer, default=50)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
