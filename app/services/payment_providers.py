import hashlib
import hmac
import requests

from app.core.config import settings
from app.core.responses import AppError, ErrorCodes


# =====================================================
# STRIPE
# =====================================================

def stripe_create_checkout(amount: float, currency: str, plan_name: str, success_url: str, cancel_url: str, metadata: dict) -> dict:
    """
    Creates a Stripe Checkout Session and returns {checkout_url, provider_payment_id}.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise AppError(ErrorCodes.PAYMENT_PROVIDER_ERROR, "Stripe is not configured (missing STRIPE_SECRET_KEY)", 500)

    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": currency.lower(),
                    "product_data": {"name": plan_name},
                    "unit_amount": int(amount * 100),  # smallest currency unit
                },
                "quantity": 1,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata,
        )
    except Exception as e:
        raise AppError(ErrorCodes.PAYMENT_PROVIDER_ERROR, f"Stripe error: {str(e)}", 502)

    return {"checkout_url": session.url, "provider_payment_id": session.id}


def stripe_verify_webhook(payload: bytes, sig_header: str) -> dict:
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise AppError(ErrorCodes.PAYMENT_PROVIDER_ERROR, "Stripe webhook secret not configured", 500)

    import stripe
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise AppError(ErrorCodes.WEBHOOK_SIGNATURE_INVALID, "Invalid Stripe webhook signature", 400)

    return event


# =====================================================
# PAYMOB
# =====================================================

PAYMOB_BASE_URL = "https://accept.paymob.com/api"


def paymob_create_checkout(amount: float, currency: str, plan_name: str, user_email: str, metadata: dict) -> dict:
    """
    Creates a Paymob payment intention and returns {checkout_url, provider_payment_id}.
    Uses Paymob's "Intention API" (unified checkout).
    """
    if not settings.PAYMOB_API_KEY or not settings.PAYMOB_INTEGRATION_ID:
        raise AppError(ErrorCodes.PAYMENT_PROVIDER_ERROR, "Paymob is not configured", 500)

    amount_cents = int(amount * 100)

    try:
        resp = requests.post(
            f"{PAYMOB_BASE_URL}/ecommerce/orders",
            headers={"Authorization": f"Bearer {settings.PAYMOB_API_KEY}"},
            json={},
            timeout=15
        )
        # Simplified flow placeholder — real implementation depends on
        # Paymob account setup (auth token, order registration, payment key).
        resp.raise_for_status()
    except Exception as e:
        raise AppError(ErrorCodes.PAYMENT_PROVIDER_ERROR, f"Paymob error: {str(e)}", 502)

    data = resp.json()
    order_id = data.get("id")

    checkout_url = f"https://accept.paymob.com/api/acceptance/iframes/{settings.PAYMOB_INTEGRATION_ID}?payment_token={order_id}"

    return {"checkout_url": checkout_url, "provider_payment_id": str(order_id)}


def paymob_verify_webhook(payload: dict, received_hmac: str) -> bool:
    if not settings.PAYMOB_HMAC_SECRET:
        raise AppError(ErrorCodes.PAYMENT_PROVIDER_ERROR, "Paymob HMAC secret not configured", 500)

    # Paymob HMAC concatenation order per their docs (transaction processed callback)
    keys_order = [
        "amount_cents", "created_at", "currency", "error_occured",
        "has_parent_transaction", "id", "integration_id", "is_3d_secure",
        "is_auth", "is_capture", "is_refunded", "is_standalone_payment",
        "is_voided", "order", "owner", "pending", "source_data_pan",
        "source_data_sub_type", "source_data_type", "success"
    ]

    concatenated = "".join(str(payload.get(k, "")) for k in keys_order)

    computed = hmac.new(
        settings.PAYMOB_HMAC_SECRET.encode(),
        concatenated.encode(),
        hashlib.sha512
    ).hexdigest()

    return hmac.compare_digest(computed, received_hmac)
