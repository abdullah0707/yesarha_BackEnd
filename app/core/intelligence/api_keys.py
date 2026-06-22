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
from app.models.specialist import SpecialistModel


def generate_api_key(specialization: str) -> str:
    """
    يولّد مفتاحاً عشوائياً آمناً بصيغة: yesk_{specialization}_{32 hex chars}
    البادئة تجعل المفتاح قابلاً للتمييز بصرياً وقابل للبحث السريع
    """
    random_part = secrets.token_hex(16)  # 32 حرف hex، عشوائية تشفيرية آمنة
    return f"yesk_{specialization}_{random_part}"


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
