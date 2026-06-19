from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from app.db.session import get_db
from app.core.deps import get_current_admin, require_super_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.core.security import hash_password
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.user import Admin

router = APIRouter(prefix="/admin/admins", tags=["Admin - Admins"])

ALL_PERMISSIONS = ["models","agents","analytics","system","admins","specialists","core"]
VALID_ROLES = ("super_admin","admin","viewer")


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


def _default_perms(role: str) -> list:
    if role == "super_admin": return ALL_PERMISSIONS
    if role == "admin": return ["models","agents","analytics","system","specialists","core"]
    return ["models","agents","analytics","system"]


@router.get("")
def list_admins(params: ListParams = Depends(), role: str | None = None,
                db: Session = Depends(get_db), _=Depends(get_current_admin)):
    q = db.query(Admin)
    if role: q = q.filter(Admin.role == role)
    if params.search:
        q = q.filter(Admin.email.ilike(f"%{params.search}%") | Admin.full_name.ilike(f"%{params.search}%"))
    q = apply_sort(q, Admin, params.sort, "id")
    items, total = apply_pagination(q, params)
    return paginated([AdminOut.model_validate(i).model_dump() for i in items], params.page, params.limit, total)


@router.post("")
def create_admin(payload: AdminCreate, db: Session = Depends(get_db), _=Depends(require_super_admin)):
    if payload.role not in VALID_ROLES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"role must be one of {VALID_ROLES}")
    if db.query(Admin).filter(Admin.email == payload.email).first():
        raise AppError(ErrorCodes.ALREADY_EXISTS, "Email already registered", 409)
    perms = ALL_PERMISSIONS if payload.role == "super_admin" else (payload.permissions or _default_perms(payload.role))
    admin = Admin(email=payload.email, password_hash=hash_password(payload.password),
                  full_name=payload.full_name, role=payload.role, permissions=perms,
                  status="active", preferred_language=payload.preferred_language)
    db.add(admin); db.commit(); db.refresh(admin)
    return success(AdminOut.model_validate(admin).model_dump())


@router.get("/{admin_id}")
def get_admin(admin_id: int, db: Session = Depends(get_db), _=Depends(get_current_admin)):
    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin: raise AppError(ErrorCodes.NOT_FOUND, "Admin not found", 404)
    return success(AdminOut.model_validate(admin).model_dump())


@router.patch("/{admin_id}")
def update_admin(admin_id: int, payload: AdminUpdate, db: Session = Depends(get_db),
                 current=Depends(require_super_admin)):
    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin: raise AppError(ErrorCodes.NOT_FOUND, "Admin not found", 404)
    if admin.id == current.id and payload.role and payload.role != "super_admin":
        raise AppError(ErrorCodes.FORBIDDEN, "Cannot change your own role", 403)
    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"]:
        admin.password_hash = hash_password(data.pop("password"))
    else: data.pop("password", None)
    if data.get("role") == "super_admin": data["permissions"] = ALL_PERMISSIONS
    for k, v in data.items(): setattr(admin, k, v)
    db.commit(); db.refresh(admin)
    return success(AdminOut.model_validate(admin).model_dump())


@router.delete("/{admin_id}")
def delete_admin(admin_id: int, db: Session = Depends(get_db), current=Depends(require_super_admin)):
    if admin_id == current.id:
        raise AppError(ErrorCodes.FORBIDDEN, "Cannot delete your own account", 403)
    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin: raise AppError(ErrorCodes.NOT_FOUND, "Admin not found", 404)
    db.delete(admin); db.commit()
    return success({"deleted": True, "id": admin_id})
