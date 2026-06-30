from datetime import datetime, timedelta
from typing import Optional
from passlib.context import CryptContext
from jose import jwt, JWTError

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_token(data: dict, expires_minutes: int, token_type: str = "access") -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire, "type": token_type})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int, role: str) -> str:
    return create_token(
        {"sub": str(user_id), "role": role, "actor": "admin"},
        settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "access"
    )


def create_refresh_token(user_id: int, role: str) -> str:
    return create_token(
        {"sub": str(user_id), "role": role, "actor": "admin"},
        settings.REFRESH_TOKEN_EXPIRE_MINUTES,
        "refresh"
    )


def create_user_access_token(user_id: int) -> str:
    return create_token(
        {"sub": str(user_id), "actor": "user"},
        settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "access"
    )


def create_user_refresh_token(user_id: int) -> str:
    return create_token(
        {"sub": str(user_id), "actor": "user"},
        settings.REFRESH_TOKEN_EXPIRE_MINUTES,
        "refresh"
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None
