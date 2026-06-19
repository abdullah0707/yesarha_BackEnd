from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import require_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.billing import Subscription
from app.schemas.subscription import SubscriptionUpdate, SubscriptionOut

router = APIRouter(prefix="/admin/subscriptions", tags=["Admin - Subscriptions"])


@router.get("")
def list_subscriptions(
    params: ListParams = Depends(),
    status: str | None = None,
    plan_id: int | None = None,
    user_id: int | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    query = db.query(Subscription)

    if status:
        query = query.filter(Subscription.status == status)
    if plan_id:
        query = query.filter(Subscription.plan_id == plan_id)
    if user_id:
        query = query.filter(Subscription.user_id == user_id)

    query = apply_sort(query, Subscription, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    return paginated([SubscriptionOut.model_validate(i).model_dump() for i in items], params.page, params.limit, total)


@router.patch("/{subscription_id}")
def update_subscription(
    subscription_id: int,
    payload: SubscriptionUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise AppError(ErrorCodes.NOT_FOUND, "Subscription not found", 404)

    data = payload.model_dump(exclude_unset=True)

    if "status" in data and data["status"] not in ("active", "cancelled", "expired"):
        raise AppError(ErrorCodes.VALIDATION_ERROR, "Invalid status value")

    for key, value in data.items():
        setattr(sub, key, value)

    db.commit()
    db.refresh(sub)

    return success(SubscriptionOut.model_validate(sub).model_dump())
