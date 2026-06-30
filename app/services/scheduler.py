"""
Background scheduler for subscription renewals.

Runs periodically (default: every hour) and checks for subscriptions
whose `renews_at` has passed. For each due subscription:
  - if auto_renew is True: re-applies the plan's credits to the wallet
    according to its rollover_policy, and sets the next renews_at.
  - if auto_renew is False: marks the subscription as 'expired'.

This does NOT charge any payment automatically (no stored payment
methods are assumed). It only handles credit allocation for plans
that are considered "active" subscriptions. If real recurring billing
via Stripe/Paymob is added later, this job should be adapted to first
attempt a charge and only renew credits on success.
"""

from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

from app.db.session import SessionLocal
from app.models.billing import Subscription, Plan, Wallet
from app.models.ledger import CreditTransaction


def _next_renewal_date(billing_cycle: str | None, base: datetime) -> datetime | None:
    if billing_cycle == "monthly":
        return base + timedelta(days=30)
    if billing_cycle == "yearly":
        return base + timedelta(days=365)
    return None


def process_due_subscriptions():
    db = SessionLocal()
    try:
        now = datetime.utcnow()

        due_subs = db.query(Subscription).filter(
            Subscription.status == "active",
            Subscription.renews_at != None,  # noqa: E711
            Subscription.renews_at <= now
        ).all()

        for sub in due_subs:
            plan = db.query(Plan).filter(Plan.id == sub.plan_id).first()
            if not plan or not plan.is_active:
                sub.status = "expired"
                db.add(sub)
                continue

            if not sub.auto_renew:
                sub.status = "expired"
                db.add(sub)
                continue

            wallet = db.query(Wallet).filter(Wallet.user_id == sub.user_id).first()
            if not wallet:
                wallet = Wallet(user_id=sub.user_id, subscription_credits=0, topup_credits=0)
                db.add(wallet)
                db.flush()

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
            wallet.plan_renewed_at = now
            db.add(wallet)
            db.flush()

            tx = CreditTransaction(
                user_id=sub.user_id,
                type="subscription_renewal",
                amount=plan.credits_amount,
                source="subscription",
                balance_after=wallet.total_credits,
                related_service=None,
                payment_id=None
            )
            db.add(tx)

            sub.renews_at = _next_renewal_date(plan.billing_cycle, now)
            sub.status = "active"
            db.add(sub)

        db.commit()
    finally:
        db.close()


_scheduler: BackgroundScheduler | None = None


def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        process_due_subscriptions,
        "interval",
        hours=1,
        id="subscription_renewal",
        replace_existing=True
    )
    _scheduler.start()


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
