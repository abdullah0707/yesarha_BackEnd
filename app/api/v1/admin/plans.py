from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import require_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.billing import Plan
from app.schemas.plan import PlanCreate, PlanUpdate, PlanOut

router = APIRouter(prefix="/admin/plans", tags=["Admin - Plans"])


@router.get("")
def list_plans(
    params: ListParams = Depends(),
    type: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    query = db.query(Plan)

    if type:
        query = query.filter(Plan.type == type)
    if is_active is not None:
        query = query.filter(Plan.is_active == is_active)
    if params.search:
        query = query.filter(Plan.name.ilike(f"%{params.search}%"))

    query = apply_sort(query, Plan, params.sort, default_field="id")
    items, total = apply_pagination(query, params)

    return paginated([PlanOut.model_validate(i).model_dump() for i in items], params.page, params.limit, total)


@router.post("")
def create_plan(payload: PlanCreate, db: Session = Depends(get_db), _admin=Depends(require_admin)):

    if payload.type not in ("subscription", "topup"):
        raise AppError(ErrorCodes.VALIDATION_ERROR, "type must be 'subscription' or 'topup'")

    if payload.type == "subscription" and not payload.rollover_policy:
        payload.rollover_policy = "reset"

    plan = Plan(**payload.model_dump())
    db.add(plan)
    db.commit()
    db.refresh(plan)

    return success(PlanOut.model_validate(plan).model_dump())


@router.get("/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise AppError(ErrorCodes.NOT_FOUND, "Plan not found", 404)
    return success(PlanOut.model_validate(plan).model_dump())


@router.patch("/{plan_id}")
def update_plan(plan_id: int, payload: PlanUpdate, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise AppError(ErrorCodes.NOT_FOUND, "Plan not found", 404)

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(plan, key, value)

    db.commit()
    db.refresh(plan)

    return success(PlanOut.model_validate(plan).model_dump())


@router.delete("/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise AppError(ErrorCodes.NOT_FOUND, "Plan not found", 404)

    db.delete(plan)
    db.commit()

    return success({"deleted": True, "id": plan_id})
