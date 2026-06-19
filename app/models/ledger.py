from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric, JSON, Float
from datetime import datetime

from app.db.session import Base


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    type = Column(String, nullable=False)
    # 'topup_purchase' | 'subscription_renewal' | 'consumption' | 'refund'

    amount = Column(Integer, nullable=False)         # positive=credit added, negative=consumed
    source = Column(String, nullable=True)            # 'subscription' | 'topup' | None
    balance_after = Column(Integer, nullable=True)

    related_service = Column(String, nullable=True)
    related_model = Column(String, nullable=True)
    related_agent = Column(String, nullable=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    service_key = Column(String, nullable=False)
    model = Column(String, nullable=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)

    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)

    credits_charged = Column(Numeric(12, 4), nullable=False, default=0)
    calculation_type = Column(String, nullable=True)

    result_status = Column(String, nullable=False, default="success")  # 'success' | 'failed'

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)

    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String, default="EGP")

    provider = Column(String, nullable=False)  # 'stripe' | 'paymob'
    provider_payment_id = Column(String, nullable=True, index=True)
    checkout_url = Column(String, nullable=True)

    status = Column(String, default="pending")  # 'pending' | 'completed' | 'failed' | 'refunded'

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
