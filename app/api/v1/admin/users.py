from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import get_current_admin, require_super_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.core.security import hash_password
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.user import Admin
from app.schemas.admin_user import AdminCreate, AdminUpdate, AdminOut, VALID_ROLES, ALL_PERMISSIONS

router = APIRouter(prefix="/admin/admins", tags=["Admin - Admins Management"])


def _default_permissions(role: str) -> list:
    if role == "super_admin":
        return ALL_PERMISSIONS
    if role == "admin":
        return ["models", "agents", "analytics", "system"]
    if role == "viewer":
        return ["models", "agents", "analytics", "system"]
    return []


@router.get("")
def list_admins(
    params: ListParams = Depends(),
    role: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    query = db.query(Admin)

    if role:
        query = query.filter(Admin.role == role)
    if status:
        query = query.filter(Admin.status == status)
    if params.search:
        query = query.filter(
            (Admin.email.ilike(f"%{params.search}%")) |
            (Admin.full_name.ilike(f"%{params.search}%"))
        )

    query = apply_sort(query, Admin, params.sort, default_field="id")
    items, total = apply_pagination(query, params)

    return paginated(
        [AdminOut.model_validate(i).model_dump() for i in items],
        params.page, params.limit, total
    )


@router.post("")
def create_admin(
    payload: AdminCreate,
    db: Session = Depends(get_db),
    _admin=Depends(require_super_admin)
):
    if payload.role not in VALID_ROLES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"role must be one of {VALID_ROLES}")

    existing = db.query(Admin).filter(Admin.email == payload.email).first()
    if existing:
        raise AppError(ErrorCodes.ALREADY_EXISTS, "Email already registered", 409)

    permissions = payload.permissions if payload.permissions is not None else _default_permissions(payload.role)
    # super_admin always gets all permissions regardless
    if payload.role == "super_admin":
        permissions = ALL_PERMISSIONS

    admin = Admin(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        permissions=permissions,
        status="active",
        preferred_language=payload.preferred_language
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    return success(AdminOut.model_validate(admin).model_dump())


@router.get("/{admin_id}")
def get_admin(
    admin_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin:
        raise AppError(ErrorCodes.NOT_FOUND, "Admin not found", 404)
    return success(AdminOut.model_validate(admin).model_dump())


@router.patch("/{admin_id}")
def update_admin(
    admin_id: int,
    payload: AdminUpdate,
    db: Session = Depends(get_db),
    current_admin=Depends(require_super_admin)
):
    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin:
        raise AppError(ErrorCodes.NOT_FOUND, "Admin not found", 404)

    # prevent super_admin from demoting themselves
    if admin.id == current_admin.id and payload.role and payload.role != "super_admin":
        raise AppError(ErrorCodes.FORBIDDEN, "Cannot change your own role", 403)

    data = payload.model_dump(exclude_unset=True)

    if "role" in data and data["role"] not in VALID_ROLES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"role must be one of {VALID_ROLES}")

    if "password" in data and data["password"]:
        admin.password_hash = hash_password(data.pop("password"))
    else:
        data.pop("password", None)

    # super_admin always gets all permissions
    if data.get("role") == "super_admin":
        data["permissions"] = ALL_PERMISSIONS

    for key, value in data.items():
        setattr(admin, key, value)

    db.commit()
    db.refresh(admin)

    return success(AdminOut.model_validate(admin).model_dump())


@router.delete("/{admin_id}")
def delete_admin(
    admin_id: int,
    db: Session = Depends(get_db),
    current_admin=Depends(require_super_admin)
):
    if admin_id == current_admin.id:
        raise AppError(ErrorCodes.FORBIDDEN, "Cannot delete your own account", 403)

    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin:
        raise AppError(ErrorCodes.NOT_FOUND, "Admin not found", 404)

    db.delete(admin)
    db.commit()

    return success({"deleted": True, "id": admin_id})
