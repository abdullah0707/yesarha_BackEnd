from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session

from app.core.responses import AppError, ErrorCodes
from app.models.billing import Wallet
from app.models.pricing import ServicePricing, CreditPolicy
from app.models.ledger import CreditTransaction, UsageLog


def _get_policy(db: Session) -> CreditPolicy:
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


def resolve_pricing(db: Session, service_key: str, model_id: int | None = None, agent_id: int | None = None) -> ServicePricing | None:
    """
    Resolve the most specific pricing rule available for a service call.
    Priority: (service_key + model_id + agent_id) > (service_key + model_id)
              > (service_key + agent_id) > (service_key only)
    """
    query = db.query(ServicePricing).filter(
        ServicePricing.service_key == service_key,
        ServicePricing.is_active == True  # noqa: E712
    )

    candidates = query.all()
    if not candidates:
        return None

    def score(p: ServicePricing) -> int:
        s = 0
        if model_id is not None and p.model_id == model_id:
            s += 2
        if agent_id is not None and p.agent_id == agent_id:
            s += 2
        if p.model_id is None:
            s += 0
        if p.agent_id is None:
            s += 0
        return s

    # prefer exact matches, fall back to generic (model_id=None, agent_id=None)
    exact = [p for p in candidates if (p.model_id == model_id or p.model_id is None) and (p.agent_id == agent_id or p.agent_id is None)]
    if not exact:
        return None

    exact.sort(key=score, reverse=True)
    return exact[0]


def estimate_cost(pricing: ServicePricing, estimated_tokens: int = 0, estimated_seconds: float = 0) -> Decimal:
    """
    Rough pre-execution cost estimate, used to check the balance
    BEFORE calling the model.
    """
    if pricing is None:
        return Decimal("0")

    if pricing.calculation_type == "fixed":
        return Decimal(pricing.credits_cost or 0)

    if pricing.calculation_type == "per_token":
        rate = Decimal(pricing.token_rate or 0)
        per_unit = pricing.tokens_per_unit or 1
        return (rate * Decimal(estimated_tokens)) / Decimal(per_unit)

    if pricing.calculation_type == "per_second":
        rate = Decimal(pricing.second_rate or 0)
        return rate * Decimal(estimated_seconds)

    return Decimal("0")


def compute_actual_cost(pricing: ServicePricing, tokens_input: int = 0, tokens_output: int = 0, latency_ms: int = 0) -> Decimal:
    """
    Final cost after execution, based on real usage data.
    """
    if pricing is None:
        return Decimal("0")

    if pricing.calculation_type == "fixed":
        return Decimal(pricing.credits_cost or 0)

    if pricing.calculation_type == "per_token":
        rate = Decimal(pricing.token_rate or 0)
        per_unit = pricing.tokens_per_unit or 1
        total_tokens = (tokens_input or 0) + (tokens_output or 0)
        return (rate * Decimal(total_tokens)) / Decimal(per_unit)

    if pricing.calculation_type == "per_second":
        rate = Decimal(pricing.second_rate or 0)
        seconds = Decimal(latency_ms or 0) / Decimal(1000)
        return rate * seconds

    return Decimal("0")


def check_limits(db: Session, user_id: int, policy: CreditPolicy):
    """
    Enforce daily/monthly consumption limits based on usage_logs.
    Raises AppError if a limit is exceeded.
    """
    now = datetime.utcnow()

    if policy.daily_limit is not None:
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used_today = db.query(UsageLog).filter(
            UsageLog.user_id == user_id,
            UsageLog.created_at >= start_of_day
        ).with_entities(UsageLog.credits_charged).all()

        total_today = sum(Decimal(u[0] or 0) for u in used_today)

        if total_today >= policy.daily_limit:
            raise AppError(ErrorCodes.DAILY_LIMIT_REACHED, "Daily usage limit reached", 429)

    if policy.monthly_limit is not None:
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used_month = db.query(UsageLog).filter(
            UsageLog.user_id == user_id,
            UsageLog.created_at >= start_of_month
        ).with_entities(UsageLog.credits_charged).all()

        total_month = sum(Decimal(u[0] or 0) for u in used_month)

        if total_month >= policy.monthly_limit:
            raise AppError(ErrorCodes.MONTHLY_LIMIT_REACHED, "Monthly usage limit reached", 429)


def ensure_sufficient_balance(wallet: Wallet, estimated: Decimal):
    if Decimal(wallet.total_credits) < estimated:
        raise AppError(
            ErrorCodes.INSUFFICIENT_CREDITS,
            "Insufficient credit balance for this operation",
            402
        )


def deduct_credits(
    db: Session,
    wallet: Wallet,
    amount: Decimal,
    policy: CreditPolicy,
    related_service: str | None = None,
    related_model: str | None = None,
    related_agent: str | None = None,
) -> CreditTransaction:
    """
    Deduct `amount` from the wallet according to the deduction priority,
    splitting across topup_credits / subscription_credits if needed.
    Records a CreditTransaction.
    """
    amount = Decimal(amount)
    remaining = amount

    if policy.deduction_priority == "subscription_first":
        order = ["subscription_credits", "topup_credits"]
    else:
        order = ["topup_credits", "subscription_credits"]

    for field in order:
        if remaining <= 0:
            break

        available = Decimal(getattr(wallet, field) or 0)
        take = min(available, remaining)

        setattr(wallet, field, int(available - take))
        remaining -= take

    db.add(wallet)

    tx = CreditTransaction(
        user_id=wallet.user_id,
        type="consumption",
        amount=-int(amount),
        source=order[0],
        balance_after=wallet.total_credits,
        related_service=related_service,
        related_model=related_model,
        related_agent=related_agent
    )
    db.add(tx)
    db.commit()
    db.refresh(wallet)

    return tx


def log_usage(
    db: Session,
    user_id: int,
    service_key: str,
    model: str | None,
    agent_id: int | None,
    tokens_input: int | None,
    tokens_output: int | None,
    latency_ms: int | None,
    credits_charged: Decimal,
    calculation_type: str | None,
    result_status: str
) -> UsageLog:
    log = UsageLog(
        user_id=user_id,
        service_key=service_key,
        model=model,
        agent_id=agent_id,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        latency_ms=latency_ms,
        credits_charged=credits_charged,
        calculation_type=calculation_type,
        result_status=result_status
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_or_create_wallet(db: Session, user_id: int) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    if not wallet:
        wallet = Wallet(user_id=user_id, subscription_credits=0, topup_credits=0)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet


def get_policy(db: Session) -> CreditPolicy:
    return _get_policy(db)
