from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class SubscriptionUpdate(BaseModel):
    status: Optional[str] = None       # active | cancelled | expired
    auto_renew: Optional[bool] = None
    end_date: Optional[datetime] = None
    renews_at: Optional[datetime] = None


class SubscriptionOut(BaseModel):
    id: int
    user_id: int
    plan_id: int
    status: str
    start_date: datetime
    end_date: Optional[datetime] = None
    renews_at: Optional[datetime] = None
    auto_renew: bool

    class Config:
        from_attributes = True
