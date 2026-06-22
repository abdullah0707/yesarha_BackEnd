from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import get_current_user
from app.core.config import settings
from app.core.responses import success, AppError, ErrorCodes
from app.models.user import User
from app.models.billing import Plan
from app.models.ledger import Payment
from app.schemas.payment import CheckoutRequest
from app.services import payment_providers
from app.services.payment_fulfillment import fulfill_payment

router = APIRouter(prefix="/payments", tags=["Payments"])

VALID_PROVIDERS = ("stripe", "paymob")


@router.post("/checkout")
def create_checkout(payload: CheckoutRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):

    if payload.provider not in VALID_PROVIDERS:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"provider must be one of {VALID_PROVIDERS}")

    plan = db.query(Plan).filter(Plan.id == payload.plan_id, Plan.is_active == True).first()  # noqa: E712
    if not plan:
        raise AppError(ErrorCodes.PLAN_NOT_FOUND, "Plan not found or inactive", 404)

    # create a pending payment record first
    payment = Payment(
        user_id=user.id,
        plan_id=plan.id,
        amount=plan.price,
        currency=plan.currency or settings.PAYMENT_CURRENCY_DEFAULT,
        provider=payload.provider,
        status="pending"
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    success_url = payload.success_url or "https://yesarha.ai/payment/success"
    cancel_url = payload.cancel_url or "https://yesarha.ai/payment/cancel"

    try:
        if payload.provider == "stripe":
            result = payment_providers.stripe_create_checkout(
                amount=float(plan.price),
                currency=payment.currency,
                plan_name=plan.name,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={"payment_id": str(payment.id), "user_id": str(user.id), "plan_id": str(plan.id)}
            )
        else:  # paymob
            result = payment_providers.paymob_create_checkout(
                amount=float(plan.price),
                currency=payment.currency,
                plan_name=plan.name,
                user_email=user.email,
                metadata={"payment_id": str(payment.id), "user_id": str(user.id), "plan_id": str(plan.id)}
            )
    except AppError:
        payment.status = "failed"
        db.commit()
        raise

    payment.checkout_url = result["checkout_url"]
    payment.provider_payment_id = result["provider_payment_id"]
    db.commit()

    return success({"checkout_url": payment.checkout_url, "payment_id": payment.id})


@router.post("/webhook/{provider}")
async def payment_webhook(provider: str, request: Request, db: Session = Depends(get_db)):

    if provider not in VALID_PROVIDERS:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"Unknown provider '{provider}'")

    if provider == "stripe":
        payload_bytes = await request.body()
        sig_header = request.headers.get("stripe-signature", "")

        event = payment_providers.stripe_verify_webhook(payload_bytes, sig_header)

        if event.get("type") == "checkout.session.completed":
            session = event["data"]["object"]
            payment_id = session.get("metadata", {}).get("payment_id")

            if payment_id:
                payment = db.query(Payment).filter(Payment.id == int(payment_id)).first()
                if payment and payment.status != "completed":
                    payment.status = "completed"
                    db.commit()
                    fulfill_payment(db, payment)

        return success({"received": True})

    # paymob
    body = await request.json()
    obj = body.get("obj", body)
    received_hmac = request.query_params.get("hmac", "")

    valid = payment_providers.paymob_verify_webhook(obj, received_hmac)
    if not valid:
        raise AppError(ErrorCodes.WEBHOOK_SIGNATURE_INVALID, "Invalid Paymob HMAC signature", 400)

    provider_payment_id = str(obj.get("order", {}).get("id") if isinstance(obj.get("order"), dict) else obj.get("order"))
    success_flag = obj.get("success")

    if success_flag:
        payment = db.query(Payment).filter(Payment.provider_payment_id == provider_payment_id).first()
        if payment and payment.status != "completed":
            payment.status = "completed"
            db.commit()
            fulfill_payment(db, payment)

    return success({"received": True})
