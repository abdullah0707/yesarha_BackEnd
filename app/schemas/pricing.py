from pydantic import BaseModel
from typing import Optional
from decimal import Decimal


class ServicePricingBase(BaseModel):
    service_key: str
    model_id: Optional[int] = None
    agent_id: Optional[int] = None
    calculation_type: str = "fixed"  # fixed | per_token | per_second
    credits_cost: Optional[Decimal] = None
    token_rate: Optional[Decimal] = None
    tokens_per_unit: Optional[int] = None
    second_rate: Optional[Decimal] = None
    is_active: bool = True


class ServicePricingCreate(ServicePricingBase):
    pass


class ServicePricingUpdate(BaseModel):
    service_key: Optional[str] = None
    model_id: Optional[int] = None
    agent_id: Optional[int] = None
    calculation_type: Optional[str] = None
    credits_cost: Optional[Decimal] = None
    token_rate: Optional[Decimal] = None
    tokens_per_unit: Optional[int] = None
    second_rate: Optional[Decimal] = None
    is_active: Optional[bool] = None


class ServicePricingOut(ServicePricingBase):
    id: int

    class Config:
        from_attributes = True


class CreditPolicyUpdate(BaseModel):
    deduction_priority: Optional[str] = None  # topup_first | subscription_first
    daily_limit: Optional[int] = None
    monthly_limit: Optional[int] = None
    low_balance_threshold: Optional[int] = None


class CreditPolicyOut(BaseModel):
    id: int
    deduction_priority: str
    daily_limit: Optional[int] = None
    monthly_limit: Optional[int] = None
    low_balance_threshold: int

    class Config:
        from_attributes = True
