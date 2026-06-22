from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.models.billing import Plan, Wallet, Subscription
from app.models.ledger import Payment, CreditTransaction


def _next_renewal_date(billing_cycle: str | None) -> datetime | None:
    now = datetime.utcnow()
    if billing_cycle == "monthly":
        return now + timedelta(days=30)
    if billing_cycle == "yearly":
        return now + timedelta(days=365)
    return None


def fulfill_payment(db: Session, payment: Payment):
    """
    Called once a payment is confirmed 'completed' (via webhook).
    Applies the plan's credits to the user's wallet and,
    for subscription plans, creates/renews the Subscription record.
    """
    plan = db.query(Plan).filter(Plan.id == payment.plan_id).first()
    if not plan:
        return

    wallet = db.query(Wallet).filter(Wallet.user_id == payment.user_id).first()
    if not wallet:
        wallet = Wallet(user_id=payment.user_id, subscription_credits=0, topup_credits=0)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)

    if plan.type == "topup":
        wallet.topup_credits = (wallet.topup_credits or 0) + plan.credits_amount
        db.add(wallet)
        db.flush()

        tx = CreditTransaction(
            user_id=payment.user_id,
            type="topup_purchase",
            amount=plan.credits_amount,
            source="topup",
            balance_after=wallet.total_credits,
            related_service=None,
            payment_id=payment.id
        )

    else:  # subscription
        # Apply rollover policy
        remaining = wallet.subscription_credits or 0

        if plan.rollover_policy == "rollover":
            new_balance = remaining + plan.credits_amount
        elif plan.rollover_policy == "cap":
            cap = plan.rollover_cap or 0
            carried = min(remaining, cap)
            new_balance = carried + plan.credits_amount
        else:  # 'reset' or None
            new_balance = plan.credits_amount

        wallet.subscription_credits = new_balance
        wallet.current_plan_id = plan.id
        wallet.plan_renewed_at = datetime.utcnow()
        db.add(wallet)
        db.flush()

        # create or update subscription record
        sub = db.query(Subscription).filter(
            Subscription.user_id == payment.user_id,
            Subscription.plan_id == plan.id
        ).first()

        renews_at = _next_renewal_date(plan.billing_cycle)

        if sub:
            sub.status = "active"
            sub.renews_at = renews_at
        else:
            sub = Subscription(
                user_id=payment.user_id,
                plan_id=plan.id,
                status="active",
                start_date=datetime.utcnow(),
                renews_at=renews_at,
                auto_renew=True
            )
            db.add(sub)

        tx = CreditTransaction(
            user_id=payment.user_id,
            type="subscription_renewal",
            amount=plan.credits_amount,
            source="subscription",
            balance_after=wallet.total_credits,
            related_service=None,
            payment_id=payment.id
        )

    db.add(wallet)
    db.add(tx)
    db.commit()
    db.refresh(wallet)
