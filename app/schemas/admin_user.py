from pydantic import BaseModel, EmailStr, Field
from typing import Optional

ALL_PERMISSIONS = ["models", "agents", "analytics", "system", "admins"]
VALID_ROLES = ("super_admin", "admin", "viewer")


class AdminCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: Optional[str] = None
    role: str = "admin"
    permissions: Optional[list[str]] = None
    preferred_language: str = "ar"


class AdminUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    permissions: Optional[list[str]] = None
    status: Optional[str] = None
    preferred_language: Optional[str] = None
    password: Optional[str] = None


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
