from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from app.db.session import get_db
from app.core.deps import require_admin
from app.core.responses import paginated
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.ledger import CreditTransaction, UsageLog, Payment

router = APIRouter(prefix="/admin", tags=["Admin - Credit Engine"])


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.get("/transactions")
def list_transactions(
    params: ListParams = Depends(),
    user_id: int | None = None,
    type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    query = db.query(CreditTransaction)

    if user_id:
        query = query.filter(CreditTransaction.user_id == user_id)
    if type:
        query = query.filter(CreditTransaction.type == type)

    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if df:
        query = query.filter(CreditTransaction.created_at >= df)
    if dt:
        query = query.filter(CreditTransaction.created_at <= dt)

    query = apply_sort(query, CreditTransaction, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    data = [{
        "id": t.id,
        "user_id": t.user_id,
        "type": t.type,
        "amount": t.amount,
        "source": t.source,
        "balance_after": t.balance_after,
        "related_service": t.related_service,
        "related_model": t.related_model,
        "related_agent": t.related_agent,
        "payment_id": t.payment_id,
        "created_at": t.created_at.isoformat()
    } for t in items]

    return paginated(data, params.page, params.limit, total)


@router.get("/usage-logs")
def list_usage_logs(
    params: ListParams = Depends(),
    user_id: int | None = None,
    service_key: str | None = None,
    model: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    query = db.query(UsageLog)

    if user_id:
        query = query.filter(UsageLog.user_id == user_id)
    if service_key:
        query = query.filter(UsageLog.service_key == service_key)
    if model:
        query = query.filter(UsageLog.model == model)

    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if df:
        query = query.filter(UsageLog.created_at >= df)
    if dt:
        query = query.filter(UsageLog.created_at <= dt)

    query = apply_sort(query, UsageLog, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    data = [{
        "id": u.id,
        "user_id": u.user_id,
        "service_key": u.service_key,
        "model": u.model,
        "agent_id": u.agent_id,
        "tokens_input": u.tokens_input,
        "tokens_output": u.tokens_output,
        "latency_ms": u.latency_ms,
        "credits_charged": float(u.credits_charged),
        "calculation_type": u.calculation_type,
        "result_status": u.result_status,
        "created_at": u.created_at.isoformat()
    } for u in items]

    return paginated(data, params.page, params.limit, total)


@router.get("/payments")
def list_payments(
    params: ListParams = Depends(),
    status: str | None = None,
    provider: str | None = None,
    user_id: int | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    query = db.query(Payment)

    if status:
        query = query.filter(Payment.status == status)
    if provider:
        query = query.filter(Payment.provider == provider)
    if user_id:
        query = query.filter(Payment.user_id == user_id)

    query = apply_sort(query, Payment, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    data = [{
        "id": p.id,
        "user_id": p.user_id,
        "plan_id": p.plan_id,
        "amount": float(p.amount),
        "currency": p.currency,
        "provider": p.provider,
        "provider_payment_id": p.provider_payment_id,
        "status": p.status,
        "created_at": p.created_at.isoformat()
    } for p in items]

    return paginated(data, params.page, params.limit, total)
