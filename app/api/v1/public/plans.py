from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.responses import success
from app.models.billing import Plan
from app.schemas.plan import PlanOut

router = APIRouter(prefix="/plans", tags=["Plans"])


@router.get("")
def list_active_plans(type: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Plan).filter(Plan.is_active == True)  # noqa: E712

    if type:
        query = query.filter(Plan.type == type)

    items = query.order_by(Plan.price.asc()).all()

    return success([PlanOut.model_validate(i).model_dump() for i in items])
