"""
نظام API Key — مفتاح لكل نموذج متخصص أو حزمة
يتحقق من صلاحية المفتاح + تاريخ الانتهاء + الحد اليومي/الشهري.
"""
import secrets
from datetime import datetime, timedelta
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
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
) -> SpecialistModel:
    """
    FastAPI dependency للـ Public API — يتحقق من مفتاح API.
    يقبل X-API-Key للمستخدمين الخارجيين، أو JWT Bearer للأدمن من Dashboard.
    """
    # مسار 1: X-API-Key (المستخدمون الخارجيون / باك إند المستخدمين)
    if x_api_key:
        specialist = db.query(SpecialistModel).filter(
            SpecialistModel.api_key == x_api_key
        ).first()
        if not specialist:
            raise AppError(ErrorCodes.UNAUTHORIZED, "مفتاح API غير صالح", 401)
        if specialist.status != "active":
            raise AppError(ErrorCodes.FORBIDDEN,
                           f"النموذج '{specialist.display_name}' غير نشط (الحالة: {specialist.status})", 403)
        if not specialist.is_public_api:
            raise AppError(ErrorCodes.FORBIDDEN, "هذا النموذج غير متاح عبر API عام", 403)
        return specialist

    # مسار 2: JWT Bearer للأدمن من لوحة التحكم (اختبار النموذج مباشرة)
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        from app.core.security import decode_token
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            # الأدمن يحدد النموذج عبر X-API-Key عادةً — هنا نُرجع أول نموذج تعليمي نشط
            specialist = db.query(SpecialistModel).filter(
                SpecialistModel.specialization == "education",
                SpecialistModel.status == "active",
            ).first()
            if specialist:
                return specialist
            raise AppError(ErrorCodes.NOT_FOUND, "لا يوجد نموذج تعليمي نشط", 404)

    raise AppError(ErrorCodes.UNAUTHORIZED, "يجب إرسال X-API-Key أو Bearer token", 401)


def get_bundle_by_api_key(
    x_api_key: str = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> SpecialistBundle:
    """
    FastAPI dependency للـ Bundle API — يتحقق من:
    1. صحة المفتاح وحالة الحزمة
    2. تاريخ الانتهاء (expires_at)
    3. الحد اليومي والشهري (daily_limit / monthly_limit)
    """
    if not x_api_key:
        raise AppError(ErrorCodes.UNAUTHORIZED, "X-API-Key header مفقود", 401)

    if not x_api_key.startswith("yesk_bundle_"):
        raise AppError(ErrorCodes.UNAUTHORIZED,
                       "هذا المفتاح ليس مفتاح حزمة — استخدم /specialist/ask للمفاتيح المباشرة", 401)

    bundle = db.query(SpecialistBundle).filter(
        SpecialistBundle.api_key == x_api_key
    ).first()

    if not bundle:
        raise AppError(ErrorCodes.UNAUTHORIZED, "مفتاح الحزمة غير صالح", 401)

    if bundle.status != "active":
        raise AppError(ErrorCodes.FORBIDDEN, f"الحزمة '{bundle.name}' غير نشطة", 403)

    _enforce_gateway_limits(bundle, db)

    return bundle


def get_pipeline_auth(
    x_api_key: str = Header(default=None, alias="X-API-Key"),
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
) -> tuple:
    """
    Auth للـ pipeline endpoint — يقبل:
    1. yesk_bundle_* → يجد education specialist من الحزمة + يطبق حدودها
    2. أي مفتاح specialist → backward compatible (مباشر لنموذج متخصص)
    3. Bearer JWT → admin testing
    يُرجع (SpecialistModel, bundle|None)
    """
    # مسار 1: Bundle key
    if x_api_key and x_api_key.startswith("yesk_bundle_"):
        bundle = db.query(SpecialistBundle).filter(
            SpecialistBundle.api_key == x_api_key
        ).first()
        if not bundle:
            raise AppError(ErrorCodes.UNAUTHORIZED, "مفتاح الحزمة غير صالح", 401)
        if bundle.status != "active":
            raise AppError(ErrorCodes.FORBIDDEN, f"الحزمة '{bundle.name}' غير نشطة", 403)
        _enforce_gateway_limits(bundle, db)
        if not bundle.specialist_ids:
            raise AppError(ErrorCodes.NOT_FOUND, "لا يوجد نماذج مرتبطة بهذه الحزمة", 404)
        specialist = db.query(SpecialistModel).filter(
            SpecialistModel.id.in_(bundle.specialist_ids),
            SpecialistModel.specialization == "education",
            SpecialistModel.status == "active",
        ).first()
        if not specialist:
            raise AppError(ErrorCodes.NOT_FOUND, "لا يوجد نموذج تعليمي نشط في هذه الحزمة", 404)
        return specialist, bundle

    # مسار 2: Specialist API key (backward compatible)
    if x_api_key:
        specialist = db.query(SpecialistModel).filter(
            SpecialistModel.api_key == x_api_key
        ).first()
        if not specialist:
            raise AppError(ErrorCodes.UNAUTHORIZED, "مفتاح API غير صالح", 401)
        if specialist.status != "active":
            raise AppError(ErrorCodes.FORBIDDEN,
                           f"النموذج '{specialist.display_name}' غير نشط (الحالة: {specialist.status})", 403)
        if not specialist.is_public_api:
            raise AppError(ErrorCodes.FORBIDDEN, "هذا النموذج غير متاح عبر API عام", 403)
        return specialist, None

    # مسار 3: Bearer JWT للأدمن
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        from app.core.security import decode_token
        jwt_payload = decode_token(token)
        if jwt_payload and jwt_payload.get("type") == "access":
            specialist = db.query(SpecialistModel).filter(
                SpecialistModel.specialization == "education",
                SpecialistModel.status == "active",
            ).first()
            if specialist:
                return specialist, None
            raise AppError(ErrorCodes.NOT_FOUND, "لا يوجد نموذج تعليمي نشط", 404)

    raise AppError(ErrorCodes.UNAUTHORIZED, "يجب إرسال X-API-Key أو Bearer token", 401)


def _enforce_gateway_limits(bundle: SpecialistBundle, db: Session) -> None:
    """يتحقق من حدود المفتاح — يرفض الطلب إذا تجاوز الحد أو انتهت الصلاحية"""
    from app.models.specialist import GatewayKeyConfig, GatewayRequestLog

    config = db.query(GatewayKeyConfig).filter(
        GatewayKeyConfig.bundle_id == bundle.id
    ).first()

    if not config:
        return  # لا قيود مضبوطة

    now = datetime.utcnow()

    # فحص تاريخ الانتهاء
    if config.expires_at and now > config.expires_at:
        raise AppError(
            ErrorCodes.FORBIDDEN,
            f"مفتاح الحزمة '{bundle.name}' انتهت صلاحيته في "
            f"{config.expires_at.strftime('%Y-%m-%d')}",
            403,
        )

    # فحص الحد اليومي
    if config.daily_limit:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = db.query(GatewayRequestLog).filter(
            GatewayRequestLog.bundle_id == bundle.id,
            GatewayRequestLog.created_at >= today_start,
            GatewayRequestLog.status != "rejected",
        ).count()
        if today_count >= config.daily_limit:
            raise AppError(
                ErrorCodes.DAILY_LIMIT_REACHED,
                f"تجاوزت الحد اليومي ({config.daily_limit} طلب) للحزمة '{bundle.name}'",
                429,
            )

    # فحص الحد الشهري
    if config.monthly_limit:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_count = db.query(GatewayRequestLog).filter(
            GatewayRequestLog.bundle_id == bundle.id,
            GatewayRequestLog.created_at >= month_start,
            GatewayRequestLog.status != "rejected",
        ).count()
        if month_count >= config.monthly_limit:
            raise AppError(
                ErrorCodes.MONTHLY_LIMIT_REACHED,
                f"تجاوزت الحد الشهري ({config.monthly_limit} طلب) للحزمة '{bundle.name}'",
                429,
            )
