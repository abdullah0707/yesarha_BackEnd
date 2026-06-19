from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import require_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.pricing import ServicePricing
from app.schemas.pricing import ServicePricingCreate, ServicePricingUpdate, ServicePricingOut

router = APIRouter(prefix="/admin/service-pricing", tags=["Admin - Credit Engine"])

VALID_CALC_TYPES = ("fixed", "per_token", "per_second")


@router.get("")
def list_service_pricing(
    params: ListParams = Depends(),
    service_key: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    query = db.query(ServicePricing)

    if service_key:
        query = query.filter(ServicePricing.service_key == service_key)
    if is_active is not None:
        query = query.filter(ServicePricing.is_active == is_active)

    query = apply_sort(query, ServicePricing, params.sort, default_field="id")
    items, total = apply_pagination(query, params)

    return paginated([ServicePricingOut.model_validate(i).model_dump() for i in items], params.page, params.limit, total)


@router.post("")
def create_service_pricing(payload: ServicePricingCreate, db: Session = Depends(get_db), _admin=Depends(require_admin)):

    if payload.calculation_type not in VALID_CALC_TYPES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"calculation_type must be one of {VALID_CALC_TYPES}")

    if payload.calculation_type == "fixed" and payload.credits_cost is None:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "credits_cost is required for 'fixed' calculation_type")

    if payload.calculation_type == "per_token" and (payload.token_rate is None or payload.tokens_per_unit is None):
        raise AppError(ErrorCodes.VALIDATION_ERROR, "token_rate and tokens_per_unit are required for 'per_token'")

    if payload.calculation_type == "per_second" and payload.second_rate is None:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "second_rate is required for 'per_second'")

    item = ServicePricing(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)

    return success(ServicePricingOut.model_validate(item).model_dump())


@router.get("/{pricing_id}")
def get_service_pricing(pricing_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    item = db.query(ServicePricing).filter(ServicePricing.id == pricing_id).first()
    if not item:
        raise AppError(ErrorCodes.NOT_FOUND, "Service pricing not found", 404)
    return success(ServicePricingOut.model_validate(item).model_dump())


@router.patch("/{pricing_id}")
def update_service_pricing(pricing_id: int, payload: ServicePricingUpdate, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    item = db.query(ServicePricing).filter(ServicePricing.id == pricing_id).first()
    if not item:
        raise AppError(ErrorCodes.NOT_FOUND, "Service pricing not found", 404)

    data = payload.model_dump(exclude_unset=True)

    if "calculation_type" in data and data["calculation_type"] not in VALID_CALC_TYPES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"calculation_type must be one of {VALID_CALC_TYPES}")

    for key, value in data.items():
        setattr(item, key, value)

    db.commit()
    db.refresh(item)

    return success(ServicePricingOut.model_validate(item).model_dump())


@router.delete("/{pricing_id}")
def delete_service_pricing(pricing_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    item = db.query(ServicePricing).filter(ServicePricing.id == pricing_id).first()
    if not item:
        raise AppError(ErrorCodes.NOT_FOUND, "Service pricing not found", 404)

    db.delete(item)
    db.commit()

    return success({"deleted": True, "id": pricing_id})
