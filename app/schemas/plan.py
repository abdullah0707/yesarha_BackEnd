from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal


class PlanBase(BaseModel):
    name: str
    type: str = Field(description="subscription | topup")
    price: Decimal = Decimal("0")
    currency: str = "EGP"
    credits_amount: int = 0

    billing_cycle: Optional[str] = None     # monthly | yearly | None
    rollover_policy: Optional[str] = None   # reset | rollover | cap
    rollover_cap: Optional[int] = None

    limits_json: Optional[dict] = None
    is_active: bool = True


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    price: Optional[Decimal] = None
    currency: Optional[str] = None
    credits_amount: Optional[int] = None
    billing_cycle: Optional[str] = None
    rollover_policy: Optional[str] = None
    rollover_cap: Optional[int] = None
    limits_json: Optional[dict] = None
    is_active: Optional[bool] = None


class PlanOut(PlanBase):
    id: int

    class Config:
        from_attributes = True
