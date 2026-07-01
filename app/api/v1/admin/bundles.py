"""
Admin API لإدارة حزم النماذج المتخصصة.
Admin ينشئ الحزمة، يختار النماذج، النظام يُولّد مفتاح API.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.core.intelligence.api_keys import generate_bundle_key
from app.models.specialist import SpecialistBundle, SpecialistModel
from app.utils.listing import ListParams, apply_sort, apply_pagination

router = APIRouter(prefix="/admin/bundles", tags=["Admin - Specialist Bundles"])


class CreateBundleRequest(BaseModel):
    name: str
    description: Optional[str] = None
    specialist_ids: list[int]
    use_orchestrator: bool = True


class UpdateBundleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    specialist_ids: Optional[list[int]] = None
    use_orchestrator: Optional[bool] = None
    status: Optional[str] = None


def _serialize(bundle: SpecialistBundle, specialists: list[dict], include_key: bool = False) -> dict:
    data = {
        "id": bundle.id,
        "name": bundle.name,
        "description": bundle.description,
        "specialist_ids": bundle.specialist_ids or [],
        "specialists": specialists,
        "use_orchestrator": bundle.use_orchestrator,
        "status": bundle.status,
        "total_requests": bundle.total_requests or 0,
        "has_api_key": bool(bundle.api_key),
        "api_key_preview": f"{bundle.api_key[:20]}***" if bundle.api_key else None,
        "created_at": bundle.created_at.isoformat(),
    }
    if include_key:
        data["api_key"] = bundle.api_key
    return data


def _get_specialists_for_bundle(bundle: SpecialistBundle, db: Session) -> list[dict]:
    if not bundle.specialist_ids:
        return []
    specs = db.query(SpecialistModel).filter(
        SpecialistModel.id.in_(bundle.specialist_ids)
    ).all()
    return [
        {"id": s.id, "name": s.name, "display_name": s.display_name,
         "specialization": s.specialization, "status": s.status}
        for s in specs
    ]


# ── List ──────────────────────────────────────────────────────────

@router.get("")
def list_bundles(
    params: ListParams = Depends(),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    query = db.query(SpecialistBundle)
    if params.search:
        query = query.filter(SpecialistBundle.name.ilike(f"%{params.search}%"))

    query = apply_sort(query, SpecialistBundle, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    result = []
    for b in items:
        specs = _get_specialists_for_bundle(b, db)
        result.append(_serialize(b, specs))

    return paginated(result, params.page, params.limit, total)


# ── Create ────────────────────────────────────────────────────────

@router.post("")
def create_bundle(
    payload: CreateBundleRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    if not payload.specialist_ids:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "يجب اختيار نموذج واحد على الأقل", 422)

    # تحقق من وجود النماذج
    existing_ids = [
        s.id for s in db.query(SpecialistModel.id)
        .filter(SpecialistModel.id.in_(payload.specialist_ids)).all()
    ]
    missing = set(payload.specialist_ids) - set(existing_ids)
    if missing:
        raise AppError(ErrorCodes.NOT_FOUND, f"النماذج التالية غير موجودة: {list(missing)}", 404)

    bundle = SpecialistBundle(
        name=payload.name,
        description=payload.description,
        specialist_ids=payload.specialist_ids,
        use_orchestrator=payload.use_orchestrator,
        status="active",
        api_key=generate_bundle_key(),
    )
    db.add(bundle)
    db.commit()
    db.refresh(bundle)

    specs = _get_specialists_for_bundle(bundle, db)
    data = _serialize(bundle, specs, include_key=True)
    data["message"] = f"✅ تم إنشاء حزمة '{bundle.name}' بنجاح. احفظ المفتاح — لن يُعرض مجدداً بالكامل."
    return success(data)


# ── Get ───────────────────────────────────────────────────────────

@router.get("/{bundle_id}")
def get_bundle(
    bundle_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    bundle = db.query(SpecialistBundle).filter(SpecialistBundle.id == bundle_id).first()
    if not bundle:
        raise AppError(ErrorCodes.NOT_FOUND, "الحزمة غير موجودة", 404)

    specs = _get_specialists_for_bundle(bundle, db)
    return success(_serialize(bundle, specs, include_key=True))


# ── Update ────────────────────────────────────────────────────────

@router.patch("/{bundle_id}")
def update_bundle(
    bundle_id: int,
    payload: UpdateBundleRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    bundle = db.query(SpecialistBundle).filter(SpecialistBundle.id == bundle_id).first()
    if not bundle:
        raise AppError(ErrorCodes.NOT_FOUND, "الحزمة غير موجودة", 404)

    if payload.specialist_ids is not None:
        if not payload.specialist_ids:
            raise AppError(ErrorCodes.VALIDATION_ERROR, "يجب اختيار نموذج واحد على الأقل", 422)
        existing_ids = [
            s.id for s in db.query(SpecialistModel.id)
            .filter(SpecialistModel.id.in_(payload.specialist_ids)).all()
        ]
        missing = set(payload.specialist_ids) - set(existing_ids)
        if missing:
            raise AppError(ErrorCodes.NOT_FOUND, f"النماذج التالية غير موجودة: {list(missing)}", 404)

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(bundle, k, v)

    db.commit()
    db.refresh(bundle)
    specs = _get_specialists_for_bundle(bundle, db)
    return success(_serialize(bundle, specs, include_key=True))


# ── Regenerate Key ────────────────────────────────────────────────

@router.post("/{bundle_id}/regenerate-key")
def regenerate_bundle_key(
    bundle_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    bundle = db.query(SpecialistBundle).filter(SpecialistBundle.id == bundle_id).first()
    if not bundle:
        raise AppError(ErrorCodes.NOT_FOUND, "الحزمة غير موجودة", 404)

    bundle.api_key = generate_bundle_key()
    db.commit()

    return success({
        "id": bundle.id,
        "api_key": bundle.api_key,
        "message": "✅ تم توليد مفتاح جديد. المفتاح القديم لم يعد صالحاً — حدّث System 1 فوراً."
    })


# ── Delete ────────────────────────────────────────────────────────

@router.delete("/{bundle_id}")
def delete_bundle(
    bundle_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    bundle = db.query(SpecialistBundle).filter(SpecialistBundle.id == bundle_id).first()
    if not bundle:
        raise AppError(ErrorCodes.NOT_FOUND, "الحزمة غير موجودة", 404)

    db.delete(bundle)
    db.commit()
    return success({"deleted": True, "id": bundle_id})
