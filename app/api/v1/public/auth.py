from datetime import datetime
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.responses import success, AppError, ErrorCodes
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token
)
from app.core.deps import get_current_admin
from app.core.rate_limit import limiter
from app.models.user import Admin
from app.schemas.auth import LoginRequest, RefreshRequest, AdminOut

router = APIRouter(prefix="/auth", tags=["Auth"])

ALL_PERMISSIONS = ["models", "agents", "analytics", "system", "admins"]


def _admin_payload(admin: Admin) -> dict:
    return AdminOut.model_validate(admin).model_dump()


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):

    admin = db.query(Admin).filter(Admin.email == payload.email).first()

    if not admin or not verify_password(payload.password, admin.password_hash):
        raise AppError(ErrorCodes.INVALID_CREDENTIALS, "Invalid email or password", 401)

    if admin.status != "active":
        raise AppError(ErrorCodes.FORBIDDEN, "Admin account is suspended", 403)

    admin.last_login_at = datetime.utcnow()
    db.commit()

    access = create_access_token(admin.id, admin.role)
    refresh = create_refresh_token(admin.id, admin.role)

    return success({
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "admin": _admin_payload(admin)
    })


@router.post("/refresh")
@limiter.limit("30/minute")
def refresh_token(request: Request, payload: RefreshRequest, db: Session = Depends(get_db)):

    decoded = decode_token(payload.refresh_token)

    if not decoded or decoded.get("type") != "refresh":
        raise AppError(ErrorCodes.TOKEN_INVALID, "Invalid or expired refresh token", 401)

    admin_id = int(decoded.get("sub"))
    admin = db.query(Admin).filter(Admin.id == admin_id).first()

    if not admin or admin.status != "active":
        raise AppError(ErrorCodes.UNAUTHORIZED, "Admin not found or inactive", 401)

    access = create_access_token(admin.id, admin.role)
    new_refresh = create_refresh_token(admin.id, admin.role)

    return success({
        "access_token": access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "admin": _admin_payload(admin)
    })


@router.get("/me")
def me(admin: Admin = Depends(get_current_admin)):
    return success(_admin_payload(admin))


@router.patch("/me")
def update_me(
    full_name: str | None = None,
    preferred_language: str | None = None,
    password: str | None = None,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
    if full_name is not None:
        admin.full_name = full_name
    if preferred_language in ("ar", "en"):
        admin.preferred_language = preferred_language
    if password:
        if len(password) < 6:
            raise AppError(ErrorCodes.VALIDATION_ERROR, "Password must be at least 6 characters")
        admin.password_hash = hash_password(password)

    db.commit()
    db.refresh(admin)
    return success(_admin_payload(admin))
