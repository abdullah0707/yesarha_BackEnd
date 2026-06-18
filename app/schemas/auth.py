from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    admin: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class AdminOut(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    role: str
    permissions: list
    status: str
    preferred_language: str

    class Config:
        from_attributes = True
