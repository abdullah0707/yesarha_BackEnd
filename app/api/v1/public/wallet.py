from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import get_current_user
from app.core.responses import success, paginated
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.user import User
from app.models.billing import Wallet, Plan
from app.models.ledger import CreditTransaction

router = APIRouter(prefix="/wallet", tags=["Wallet"])


@router.get("")
def get_wallet(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()

    if not wallet:
        wallet = Wallet(user_id=user.id, subscription_credits=0, topup_credits=0)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)

    plan = None
    if wallet.current_plan_id:
        plan = db.query(Plan).filter(Plan.id == wallet.current_plan_id).first()

    return success({
        "subscription_credits": wallet.subscription_credits,
        "topup_credits": wallet.topup_credits,
        "total_credits": wallet.total_credits,
        "current_plan": {
            "id": plan.id,
            "name": plan.name,
            "billing_cycle": plan.billing_cycle
        } if plan else None,
        "renews_at": wallet.plan_renewed_at.isoformat() if wallet.plan_renewed_at else None
    })


@router.get("/transactions")
def get_my_transactions(
    params: ListParams = Depends(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    query = db.query(CreditTransaction).filter(CreditTransaction.user_id == user.id)
    query = apply_sort(query, CreditTransaction, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    data = [{
        "id": t.id,
        "type": t.type,
        "amount": t.amount,
        "source": t.source,
        "balance_after": t.balance_after,
        "related_service": t.related_service,
        "related_model": t.related_model,
        "related_agent": t.related_agent,
        "created_at": t.created_at.isoformat()
    } for t in items]

    return paginated(data, params.page, params.limit, total)
