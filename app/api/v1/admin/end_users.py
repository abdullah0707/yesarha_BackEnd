"""
Admin endpoint لإدارة المستخدمين النهائيين (System 2 / Phase 5).
يُتيح للأدمن رؤية وإدارة حسابات المستخدمين من لوحة التحكم.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import require_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.user import User
from app.models.billing import Wallet, Subscription

router = APIRouter(prefix="/admin/users", tags=["Admin - End Users"])


def _user_out(user: User, db: Session) -> dict:
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    active_sub = db.query(Subscription).filter(
        Subscription.user_id == user.id,
        Subscription.status == "active"
    ).first()
    return {
        "id":           user.id,
        "email":        user.email,
        "full_name":    user.full_name,
        "status":       user.status,
        "preferred_language": user.preferred_language,
        "created_at":   user.created_at.isoformat(),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "wallet": {
            "total_credits":         wallet.total_credits if wallet else 0,
            "subscription_credits":  wallet.subscription_credits if wallet else 0,
            "topup_credits":         wallet.topup_credits if wallet else 0,
        } if wallet else None,
        "active_plan_id": active_sub.plan_id if active_sub else None,
    }


@router.get("")
def list_users(
    params: ListParams = Depends(),
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    query = db.query(User)
    if status:
        query = query.filter(User.status == status)
    if params.search:
        query = query.filter(
            User.email.ilike(f"%{params.search}%") |
            User.full_name.ilike(f"%{params.search}%")
        )
    query = apply_sort(query, User, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)
    return paginated([_user_out(u, db) for u in items], params.page, params.limit, total)


@router.get("/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppError(ErrorCodes.NOT_FOUND, "User not found", 404)
    return success(_user_out(user, db))


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppError(ErrorCodes.NOT_FOUND, "User not found", 404)

    if status and status not in ("active", "suspended", "deleted"):
        raise AppError(ErrorCodes.VALIDATION_ERROR, "Invalid status")

    if status:
        user.status = status

    db.commit()
    db.refresh(user)
    return success(_user_out(user, db))


@router.post("/{user_id}/add-credits")
def add_credits(
    user_id: int,
    amount: int,
    credit_type: str = "topup",
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """يضيف كريدت يدوياً للمستخدم (مناسب للتجربة المحلية والـ customer support)."""
    if amount <= 0:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "amount يجب أن يكون أكبر من صفر")
    if credit_type not in ("topup", "subscription"):
        raise AppError(ErrorCodes.VALIDATION_ERROR, "credit_type: topup | subscription")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppError(ErrorCodes.NOT_FOUND, "User not found", 404)

    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    if not wallet:
        wallet = Wallet(user_id=user_id, subscription_credits=0, topup_credits=0)
        db.add(wallet)
        db.flush()

    if credit_type == "topup":
        wallet.topup_credits = (wallet.topup_credits or 0) + amount
    else:
        wallet.subscription_credits = (wallet.subscription_credits or 0) + amount

    db.commit()
    db.refresh(wallet)

    return success({
        "user_id":              user_id,
        "added":                amount,
        "type":                 credit_type,
        "new_total_credits":    wallet.total_credits,
    })
