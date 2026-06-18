from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric, JSON
)
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.session import Base


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)              # 'subscription' | 'topup'
    price = Column(Numeric(12, 2), nullable=False, default=0)
    currency = Column(String, default="EGP")
    credits_amount = Column(Integer, nullable=False, default=0)

    billing_cycle = Column(String, nullable=True)       # 'monthly' | 'yearly' | None
    rollover_policy = Column(String, nullable=True)     # 'reset' | 'rollover' | 'cap'
    rollover_cap = Column(Integer, nullable=True)

    limits_json = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)

    status = Column(String, default="active")  # 'active' | 'cancelled' | 'expired'
    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=True)
    renews_at = Column(DateTime, nullable=True)
    auto_renew = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="subscriptions")
    plan = relationship("Plan")


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    subscription_credits = Column(Integer, default=0)
    topup_credits = Column(Integer, default=0)

    current_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    plan_renewed_at = Column(DateTime, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="wallet")
    current_plan = relationship("Plan")

    @property
    def total_credits(self) -> int:
        return (self.subscription_credits or 0) + (self.topup_credits or 0)
