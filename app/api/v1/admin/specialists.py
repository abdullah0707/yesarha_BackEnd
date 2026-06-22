"""
Admin API للنماذج المتخصصة
إدارة كاملة من لوحة التحكم — إنشاء، تفعيل API Key، مراقبة الأداء
"""
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.models.user import Admin
from app.models.specialist import SpecialistModel, ModelPerformanceLog, TrainingSession, CoreTask
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.core.intelligence.specializations import VALID_SPECIALIZATIONS, SPECIALIZATIONS, get_base_model, get_vram_required
from app.core.intelligence.api_keys import generate_api_key

router = APIRouter(prefix="/admin/specialists", tags=["Admin - Specialist Models"])


class CreateSpecialistRequest(BaseModel):
    name: str
    display_name: str
    display_name_ar: Optional[str] = None
    specialization: str
    description: Optional[str] = None
    description_ar: Optional[str] = None
    uses_external_content: bool = False
    content_source_url: Optional[str] = None
    content_source_api_key: Optional[str] = None


class UpdateSpecialistRequest(BaseModel):
    display_name: Optional[str] = None
    system_prompt: Optional[str] = None
    status: Optional[str] = None
    is_public_api: Optional[bool] = None
    config_json: Optional[dict] = None
    uses_external_content: Optional[bool] = None
    content_source_url: Optional[str] = None
    content_source_api_key: Optional[str] = None


def _serialize(m: SpecialistModel, include_key: bool = False) -> dict:
    data = {
        "id": m.id,
        "name": m.name,
        "display_name": m.display_name,
        "display_name_ar": m.display_name_ar,
        "specialization": m.specialization,
        "status": m.status,
        "base_model": m.base_model,
        "api_endpoint": m.api_endpoint,
        "is_public_api": m.is_public_api,
        "has_api_key": bool(m.api_key),
        "total_requests": m.total_requests,
        "success_rate": m.success_rate,
        "vram_required_gb": m.vram_required_gb,
        "uses_external_content": m.uses_external_content,
        "content_source_url": m.content_source_url,
        "has_content_source_key": bool(m.content_source_api_key),
        "created_at": m.created_at.isoformat(),
    }
    if include_key:
        data["api_key"] = m.api_key
        data["content_source_api_key"] = m.content_source_api_key
    return data


@router.get("/specializations")
def list_specializations(_admin=Depends(get_current_admin)):
    """يُرجع كل التخصصات المتاحة لاختيارها عند إنشاء نموذج جديد — تُستخدم لبناء قائمة الاختيار في اللوحة"""
    return success([
        {"key": key, "label_ar": v["label_ar"], "label_en": v["label_en"]}
        for key, v in SPECIALIZATIONS.items()
    ])


@router.get("")
def list_specialists(
    params: ListParams = Depends(),
    specialization: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    query = db.query(SpecialistModel)
    if specialization:
        query = query.filter(SpecialistModel.specialization == specialization)
    if status:
        query = query.filter(SpecialistModel.status == status)
    if params.search:
        query = query.filter(SpecialistModel.name.ilike(f"%{params.search}%"))

    query = apply_sort(query, SpecialistModel, params.sort or "-created_at", default_field="id")
    items, total = apply_pagination(query, params)

    return paginated([_serialize(m) for m in items], params.page, params.limit, total)


@router.post("")
def create_specialist(
    payload: CreateSpecialistRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
    """
    إنشاء نموذج متخصص جديد.
    Core يبحث تلقائياً عن أفضل النماذج والمعلومات، ويولّد API Key فور التفعيل.
    """
    existing = db.query(SpecialistModel).filter(
        SpecialistModel.name == payload.name
    ).first()
    if existing:
        raise AppError(ErrorCodes.ALREADY_EXISTS, f"النموذج '{payload.name}' موجود بالفعل", 409)

    if payload.specialization not in VALID_SPECIALIZATIONS:
        raise AppError(ErrorCodes.VALIDATION_ERROR,
                       f"specialization يجب أن يكون من: {VALID_SPECIALIZATIONS}")

    specialist = SpecialistModel(
        name=payload.name,
        display_name=payload.display_name,
        display_name_ar=payload.display_name_ar,
        specialization=payload.specialization,
        description=payload.description,
        description_ar=payload.description_ar,
        base_model=get_base_model(payload.specialization),
        vram_required_gb=get_vram_required(payload.specialization),
        status="creating",
        api_endpoint=f"/api/v1/specialist/{payload.name.replace('yesarha-', '')}",
        is_public_api=True,
        created_by_core=False,
        uses_external_content=payload.uses_external_content,
        content_source_url=payload.content_source_url,
        content_source_api_key=payload.content_source_api_key,
    )
    db.add(specialist)
    db.commit()
    db.refresh(specialist)

    task = CoreTask(
        task_type="model_creation",
        target_model_id=specialist.id,
        status="pending",
        input_data={"specialization": payload.specialization, "name": payload.name}
    )
    db.add(task)
    db.commit()

    background_tasks.add_task(_background_specialist_setup, specialist.id)

    return success({
        "id": specialist.id,
        "name": specialist.name,
        "status": specialist.status,
        "message": f"✅ بدأ إنشاء النموذج '{specialist.display_name}'. Core يبحث عن أفضل المعلومات..."
    })


def _background_specialist_setup(specialist_id: int):
    """
    تُشغَّل في الخلفية: Core يبحث عن معرفة التخصص، يبني system prompt،
    يُفعّل النموذج، ويولّد API Key تلقائياً فور التفعيل.
    """
    from app.db.session import SessionLocal
    db = SessionLocal()
    specialist = None
    try:
        specialist = db.query(SpecialistModel).filter(
            SpecialistModel.id == specialist_id
        ).first()
        if not specialist:
            return

        from app.services.web.searxng_client import WebIntelligence
        from app.core.intelligence.tool_executor import ToolExecutor

        web = WebIntelligence(db=db)
        knowledge = web.search_for_specialist(specialist.specialization)

        executor = ToolExecutor(db=db)
        system_prompt = executor._build_specialist_prompt(
            specialist.specialization,
            specialist.display_name,
            knowledge.get("knowledge_base", "")
        )

        specialist.system_prompt = system_prompt
        specialist.training_data_sources = knowledge.get("sources", [])
        specialist.status = "active"

        # توليد API Key تلقائياً فور التفعيل — جاهز للاستخدام مباشرة
        if not specialist.api_key:
            specialist.api_key = generate_api_key(specialist.specialization)

        db.commit()

    except Exception:
        if specialist:
            specialist.status = "error"
            db.commit()
    finally:
        db.close()


@router.get("/{specialist_id}")
def get_specialist(
    specialist_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)

    data = _serialize(spec, include_key=True)
    data.update({
        "description": spec.description,
        "system_prompt": spec.system_prompt,
        "config_json": spec.config_json,
        "training_data_sources": spec.training_data_sources,
        "last_trained_at": spec.last_trained_at.isoformat() if spec.last_trained_at else None,
    })
    return success(data)


@router.patch("/{specialist_id}")
def update_specialist(
    specialist_id: int,
    payload: UpdateSpecialistRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)

    data = payload.model_dump(exclude_unset=True)

    # إذا تم تفعيل النموذج يدوياً ولا يملك مفتاحاً بعد، نولّد واحداً
    if data.get("status") == "active" and not spec.api_key:
        spec.api_key = generate_api_key(spec.specialization)

    for k, v in data.items():
        setattr(spec, k, v)

    db.commit()
    db.refresh(spec)
    return success(_serialize(spec, include_key=True))


@router.post("/{specialist_id}/regenerate-key")
def regenerate_api_key(
    specialist_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """يُلغي المفتاح الحالي ويولّد مفتاحاً جديداً — للحالات الأمنية (تسريب المفتاح مثلاً)"""
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)

    spec.api_key = generate_api_key(spec.specialization)
    db.commit()

    return success({
        "id": spec.id,
        "api_key": spec.api_key,
        "message": "✅ تم توليد مفتاح API جديد. المفتاح القديم لم يعد صالحاً."
    })


@router.delete("/{specialist_id}")
def delete_specialist(
    specialist_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)
    db.delete(spec)
    db.commit()
    return success({"deleted": True, "id": specialist_id})


@router.get("/{specialist_id}/performance")
def get_performance(
    specialist_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)

    logs = db.query(ModelPerformanceLog).filter(
        ModelPerformanceLog.model_id == specialist_id
    ).order_by(ModelPerformanceLog.created_at.desc()).limit(200).all()

    if not logs:
        return success({"message": "لا توجد بيانات أداء بعد", "total": 0})

    success_logs = [l for l in logs if l.status == "success"]
    all_issues = []
    for log in logs:
        if log.issues_detected:
            all_issues.extend(log.issues_detected)

    return success({
        "model": spec.display_name,
        "total_requests": len(logs),
        "success_rate": f"{(len(success_logs) / len(logs)) * 100:.1f}%",
        "avg_response_ms": int(sum(l.response_ms or 0 for l in logs) / len(logs)),
        "avg_quality_score": round(
            sum(l.quality_score or 0 for l in logs if l.quality_score) /
            max(len([l for l in logs if l.quality_score]), 1), 3
        ),
        "common_issues": list(set(all_issues))[:10],
        "recent_logs": [{
            "status": l.status,
            "response_ms": l.response_ms,
            "quality_score": l.quality_score,
            "language": l.language,
            "created_at": l.created_at.isoformat()
        } for l in logs[:20]]
    })


@router.post("/{specialist_id}/trigger-training")
def trigger_training(
    specialist_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """يطلق جلسة تدريب يدوية على النموذج"""
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)

    session = TrainingSession(
        model_id=specialist_id,
        session_type="prompt",
        status="pending"
    )
    db.add(session)
    db.commit()

    background_tasks.add_task(_background_specialist_setup, specialist_id)

    return success({
        "message": f"بدأ Core في تحديث '{spec.display_name}' بأحدث المعلومات من الإنترنت",
        "session_id": session.id
    })
