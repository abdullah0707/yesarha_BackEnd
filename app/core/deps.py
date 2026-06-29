from datetime import datetime
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import decode_token
from app.core.responses import AppError, ErrorCodes
from app.models.user import Admin
from app.models.specialist import SpecialistModel


def get_current_admin(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db)
) -> Admin:

    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(ErrorCodes.UNAUTHORIZED, "Missing or invalid Authorization header", 401)

    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)

    if not payload:
        raise AppError(ErrorCodes.TOKEN_INVALID, "Invalid or expired token", 401)

    if payload.get("type") != "access":
        raise AppError(ErrorCodes.TOKEN_INVALID, "Invalid token type", 401)

    admin_id = payload.get("sub")
    admin = db.query(Admin).filter(Admin.id == int(admin_id)).first()

    if not admin:
        raise AppError(ErrorCodes.UNAUTHORIZED, "Admin not found", 401)

    if admin.status != "active":
        raise AppError(ErrorCodes.FORBIDDEN, "Admin account is suspended", 403)

    # update last login
    admin.last_login_at = datetime.utcnow()
    db.commit()

    return admin


def require_super_admin(admin: Admin = Depends(get_current_admin)) -> Admin:
    if admin.role != "super_admin":
        raise AppError(ErrorCodes.FORBIDDEN, "Super admin privileges required", 403)
    return admin


def require_permission(permission: str):
    """
    Returns a dependency that checks if the current admin has a specific permission.
    super_admin always passes. viewer and admin are checked against permissions list.
    """
    def _check(admin: Admin = Depends(get_current_admin)) -> Admin:
        if admin.role == "super_admin":
            return admin
        if admin.role == "viewer" and permission not in (admin.permissions or []):
            raise AppError(ErrorCodes.FORBIDDEN, f"Permission '{permission}' required", 403)
        if admin.role == "admin" and permission == "admins":
            raise AppError(ErrorCodes.FORBIDDEN, "Only super_admin can manage admins", 403)
        return admin
    return _check


def get_api_key_specialist(
    x_api_key: str = Header(default=None, alias="X-API-Key"),
    authorization: str = Header(default=None),
    db: Session = Depends(get_db)
) -> SpecialistModel:
    """
    يتحقق من X-API-Key للوصول لـ specialist endpoints.
    يقبل أيضاً Bearer token للأدمن من لوحة التحكم.
    """
    # أولاً: جرب X-API-Key
    if x_api_key:
        spec = db.query(SpecialistModel).filter(
            SpecialistModel.api_key == x_api_key,
            SpecialistModel.status == "active"
        ).first()
        if spec:
            return spec
        raise AppError(ErrorCodes.UNAUTHORIZED, "API Key غير صالح أو النموذج غير نشط", 401)

    # ثانياً: Bearer token للأدمن (من لوحة التحكم)
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        from app.core.security import decode_token
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            # الأدمن يقدر يستخدم أي نموذج voice نشط
            spec = db.query(SpecialistModel).filter(
                SpecialistModel.specialization == "voice",
                SpecialistModel.status == "active"
            ).first()
            if spec:
                return spec
            raise AppError(ErrorCodes.NOT_FOUND, "نموذج الصوت غير نشط", 404)

    raise AppError(ErrorCodes.UNAUTHORIZED, "يجب إرسال X-API-Key أو Bearer token", 401)
