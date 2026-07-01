"""
نظام API Key بسيط — مفتاح واحد لكل نموذج متخصص
يُستخدم للسماح للمستخدمين النهائيين (عبر تطبيق أو موقع يسرها)
باستدعاء نموذج متخصص مباشرة دون الحاجة لتوكن أدمن.
"""
import secrets
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.responses import AppError, ErrorCodes
from app.models.specialist import SpecialistModel, SpecialistBundle


def generate_api_key(specialization: str) -> str:
    """مفتاح لنموذج متخصص: yesk_{specialization}_{32 hex chars}"""
    random_part = secrets.token_hex(16)
    return f"yesk_{specialization}_{random_part}"


def generate_bundle_key() -> str:
    """مفتاح لحزمة متخصصين: yesk_bundle_{32 hex chars}"""
    random_part = secrets.token_hex(16)
    return f"yesk_bundle_{random_part}"


def get_specialist_by_api_key(
    x_api_key: str = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> SpecialistModel:
    """
    FastAPI dependency للـ Public API — يتحقق من مفتاح API
    ويُرجع النموذج المتخصص المرتبط به إن كان صالحاً ونشطاً.
    """
    if not x_api_key:
        raise AppError(ErrorCodes.UNAUTHORIZED, "X-API-Key header مفقود", 401)

    specialist = db.query(SpecialistModel).filter(
        SpecialistModel.api_key == x_api_key
    ).first()

    if not specialist:
        raise AppError(ErrorCodes.UNAUTHORIZED, "مفتاح API غير صالح", 401)

    if specialist.status != "active":
        raise AppError(ErrorCodes.FORBIDDEN,
                       f"النموذج '{specialist.display_name}' غير نشط حالياً (الحالة: {specialist.status})", 403)

    if not specialist.is_public_api:
        raise AppError(ErrorCodes.FORBIDDEN, "هذا النموذج غير متاح عبر API عام", 403)

    return specialist


def get_bundle_by_api_key(
    x_api_key: str = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> SpecialistBundle:
    """FastAPI dependency للـ Bundle API — يتحقق من مفتاح الحزمة"""
    if not x_api_key:
        raise AppError(ErrorCodes.UNAUTHORIZED, "X-API-Key header مفقود", 401)

    if not x_api_key.startswith("yesk_bundle_"):
        raise AppError(ErrorCodes.UNAUTHORIZED, "هذا المفتاح ليس مفتاح حزمة — استخدم /specialist/ask للمفاتيح المباشرة", 401)

    bundle = db.query(SpecialistBundle).filter(
        SpecialistBundle.api_key == x_api_key
    ).first()

    if not bundle:
        raise AppError(ErrorCodes.UNAUTHORIZED, "مفتاح الحزمة غير صالح", 401)

    if bundle.status != "active":
        raise AppError(ErrorCodes.FORBIDDEN, f"الحزمة '{bundle.name}' غير نشطة", 403)

    return bundle
