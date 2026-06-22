from pydantic import BaseModel
from typing import Optional


class CheckoutRequest(BaseModel):
    plan_id: int
    provider: str = "stripe"  # 'stripe' | 'paymob'
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutResponse(BaseModel):
    checkout_url: str
    payment_id: int
