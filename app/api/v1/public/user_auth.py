"""
User Authentication — System 2 / Phase 5
Endpoints للمستخدمين النهائيين، منفصلة تماماً عن auth الأدمن.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from app.db.session import get_db
from app.core.security import hash_password, verify_password, create_user_access_token, create_user_refresh_token, decode_token
from app.core.responses import success, AppError, ErrorCodes
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.models.user import User
from app.models.billing import Wallet

router = APIRouter(prefix="/user/auth", tags=["User - Auth"])


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: Optional[str] = None
    preferred_language: str = "ar"


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserRefreshRequest(BaseModel):
    refresh_token: str


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "preferred_language": user.preferred_language,
        "status": user.status,
        "created_at": user.created_at.isoformat(),
    }


@router.post("/register")
@limiter.limit("5/minute")
def register(request: Request, payload: UserRegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise AppError(ErrorCodes.ALREADY_EXISTS, "البريد الإلكتروني مسجَّل مسبقاً", 409)

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        preferred_language=payload.preferred_language,
        status="active",
    )
    db.add(user)
    db.flush()

    wallet = Wallet(user_id=user.id, subscription_credits=0, topup_credits=0)
    db.add(wallet)
    db.commit()
    db.refresh(user)

    access  = create_user_access_token(user.id)
    refresh = create_user_refresh_token(user.id)

    return success({
        "access_token":  access,
        "refresh_token": refresh,
        "token_type":    "bearer",
        "user":          _user_payload(user),
    })


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, payload: UserLoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()

    if not user or not verify_password(payload.password, user.password_hash):
        raise AppError(ErrorCodes.INVALID_CREDENTIALS, "بريد إلكتروني أو كلمة مرور غير صحيحة", 401)

    if user.status != "active":
        raise AppError(ErrorCodes.FORBIDDEN, "الحساب موقوف", 403)

    user.last_login_at = datetime.utcnow()
    db.commit()

    access  = create_user_access_token(user.id)
    refresh = create_user_refresh_token(user.id)

    return success({
        "access_token":  access,
        "refresh_token": refresh,
        "token_type":    "bearer",
        "user":          _user_payload(user),
    })


@router.post("/refresh")
@limiter.limit("30/minute")
def refresh_token(request: Request, payload: UserRefreshRequest, db: Session = Depends(get_db)):
    decoded = decode_token(payload.refresh_token)

    if not decoded or decoded.get("type") != "refresh":
        raise AppError(ErrorCodes.TOKEN_INVALID, "Refresh token غير صالح أو منتهي الصلاحية", 401)

    if decoded.get("actor") != "user":
        raise AppError(ErrorCodes.UNAUTHORIZED, "يجب استخدام user refresh token", 401)

    user_id = int(decoded.get("sub"))
    user = db.query(User).filter(User.id == user_id).first()

    if not user or user.status != "active":
        raise AppError(ErrorCodes.UNAUTHORIZED, "المستخدم غير موجود أو موقوف", 401)

    access  = create_user_access_token(user.id)
    refresh = create_user_refresh_token(user.id)

    return success({
        "access_token":  access,
        "refresh_token": refresh,
        "token_type":    "bearer",
    })


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return success(_user_payload(user))


@router.patch("/me")
def update_me(
    full_name: Optional[str] = None,
    preferred_language: Optional[str] = None,
    password: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if full_name is not None:
        user.full_name = full_name
    if preferred_language in ("ar", "en"):
        user.preferred_language = preferred_language
    if password:
        if len(password) < 6:
            raise AppError(ErrorCodes.VALIDATION_ERROR, "كلمة المرور يجب أن تكون 6 أحرف على الأقل")
        user.password_hash = hash_password(password)

    db.commit()
    db.refresh(user)
    return success(_user_payload(user))
