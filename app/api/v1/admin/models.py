from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.models.ai import AIModel
from app.schemas.ai import AIModelCreate, AIModelUpdate, AIModelOut

router = APIRouter(prefix="/admin/models", tags=["Admin - Models"])

VALID_TYPES = ("reasoning", "planning", "general", "vision")
VALID_STATUS = ("active", "inactive")


@router.get("")
def list_models(
    params: ListParams = Depends(),
    type: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    query = db.query(AIModel)

    if type:
        query = query.filter(AIModel.type == type)
    if status:
        query = query.filter(AIModel.status == status)
    if params.search:
        query = query.filter(AIModel.name.ilike(f"%{params.search}%"))

    query = apply_sort(query, AIModel, params.sort, default_field="id")
    items, total = apply_pagination(query, params)

    return paginated([AIModelOut.model_validate(i).model_dump() for i in items], params.page, params.limit, total)


@router.post("")
def create_model(payload: AIModelCreate, db: Session = Depends(get_db), _admin=Depends(get_current_admin)):

    if payload.type not in VALID_TYPES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"type must be one of {VALID_TYPES}")
    if payload.status not in VALID_STATUS:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"status must be one of {VALID_STATUS}")

    if payload.is_default:
        db.query(AIModel).filter(AIModel.is_default == True).update({"is_default": False})  # noqa: E712

    model = AIModel(**payload.model_dump())
    db.add(model)
    db.commit()
    db.refresh(model)

    return success(AIModelOut.model_validate(model).model_dump())


@router.get("/{model_id}")
def get_model(model_id: int, db: Session = Depends(get_db), _admin=Depends(get_current_admin)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise AppError(ErrorCodes.NOT_FOUND, "Model not found", 404)
    return success(AIModelOut.model_validate(model).model_dump())


@router.patch("/{model_id}")
def update_model(model_id: int, payload: AIModelUpdate, db: Session = Depends(get_db), _admin=Depends(get_current_admin)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise AppError(ErrorCodes.NOT_FOUND, "Model not found", 404)

    data = payload.model_dump(exclude_unset=True)

    if "type" in data and data["type"] not in VALID_TYPES:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"type must be one of {VALID_TYPES}")
    if "status" in data and data["status"] not in VALID_STATUS:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"status must be one of {VALID_STATUS}")

    if data.get("is_default") is True:
        db.query(AIModel).filter(AIModel.id != model_id, AIModel.is_default == True).update({"is_default": False})  # noqa: E712

    for key, value in data.items():
        setattr(model, key, value)

    db.commit()
    db.refresh(model)

    return success(AIModelOut.model_validate(model).model_dump())


@router.delete("/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db), _admin=Depends(get_current_admin)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise AppError(ErrorCodes.NOT_FOUND, "Model not found", 404)

    db.delete(model)
    db.commit()

    return success({"deleted": True, "id": model_id})
