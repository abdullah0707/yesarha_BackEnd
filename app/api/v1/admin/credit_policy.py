from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import require_admin
from app.core.responses import success, AppError, ErrorCodes
from app.models.pricing import CreditPolicy
from app.schemas.pricing import CreditPolicyUpdate, CreditPolicyOut

router = APIRouter(prefix="/admin/credit-policy", tags=["Admin - Credit Engine"])

VALID_PRIORITIES = ("topup_first", "subscription_first")


def _get_or_create_policy(db: Session) -> CreditPolicy:
    policy = db.query(CreditPolicy).first()
    if not policy:
        policy = CreditPolicy(
            deduction_priority="topup_first",
            daily_limit=None,
            monthly_limit=None,
            low_balance_threshold=50
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
    return policy


@router.get("")
def get_credit_policy(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    policy = _get_or_create_policy(db)
    return success(CreditPolicyOut.model_validate(policy).model_dump())


@router.patch("")
def update_credit_policy(payload: CreditPolicyUpdate, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    policy = _get_or_create_policy(db)

    data = payload.model_dump(exclude_unset=True)

    if "deduction_priority" in data and data["deduction_priority"] not in VALID_PRIORITIES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"deduction_priority must be one of {VALID_PRIORITIES}")

    for key, value in data.items():
        setattr(policy, key, value)

    db.commit()
    db.refresh(policy)

    return success(CreditPolicyOut.model_validate(policy).model_dump())
