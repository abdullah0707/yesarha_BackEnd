"""
Admin API للنماذج المتخصصة
إدارة كاملة من لوحة التحكم
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

router = APIRouter(prefix="/admin/specialists", tags=["Admin - Specialist Models"])


class CreateSpecialistRequest(BaseModel):
    name: str
    display_name: str
    display_name_ar: Optional[str] = None
    specialization: str
    description: Optional[str] = None
    description_ar: Optional[str] = None


class UpdateSpecialistRequest(BaseModel):
    display_name: Optional[str] = None
    system_prompt: Optional[str] = None
    status: Optional[str] = None
    is_public_api: Optional[bool] = None
    config_json: Optional[dict] = None


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

    return paginated([{
        "id": m.id,
        "name": m.name,
        "display_name": m.display_name,
        "display_name_ar": m.display_name_ar,
        "specialization": m.specialization,
        "status": m.status,
        "base_model": m.base_model,
        "api_endpoint": m.api_endpoint,
        "is_public_api": m.is_public_api,
        "total_requests": m.total_requests,
        "success_rate": m.success_rate,
        "vram_required_gb": m.vram_required_gb,
        "created_at": m.created_at.isoformat(),
    } for m in items], params.page, params.limit, total)


@router.post("")
def create_specialist(
    payload: CreateSpecialistRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
    """
    إنشاء نموذج متخصص جديد.
    Core يبحث تلقائياً عن أفضل النماذج والمعلومات.
    """
    existing = db.query(SpecialistModel).filter(
        SpecialistModel.name == payload.name
    ).first()
    if existing:
        raise AppError(ErrorCodes.ALREADY_EXISTS, f"النموذج '{payload.name}' موجود بالفعل", 409)

    VALID_SPECS = ["code", "voice", "image", "education", "custom"]
    if payload.specialization not in VALID_SPECS:
        raise AppError(ErrorCodes.VALIDATION_ERROR, f"specialization يجب أن يكون من: {VALID_SPECS}")

    base_models = {
        "code": "qwen2.5-coder:7b",
        "voice": "qwen3:8b",
        "image": "qwen3:8b",
        "education": "qwen3:8b",
        "custom": "qwen3:8b",
    }

    specialist = SpecialistModel(
        name=payload.name,
        display_name=payload.display_name,
        display_name_ar=payload.display_name_ar,
        specialization=payload.specialization,
        description=payload.description,
        description_ar=payload.description_ar,
        base_model=base_models.get(payload.specialization, "qwen3:8b"),
        status="creating",
        api_endpoint=f"/api/v1/specialist/{payload.name.replace('yesarha-', '')}",
        is_public_api=True,
        created_by_core=False,
    )
    db.add(specialist)
    db.commit()
    db.refresh(specialist)

    # إضافة مهمة بحث تلقائي في الخلفية
    task = CoreTask(
        task_type="model_creation",
        target_model_id=specialist.id,
        status="pending",
        input_data={"specialization": payload.specialization, "name": payload.name}
    )
    db.add(task)
    db.commit()

    # تشغيل البحث في الخلفية
    background_tasks.add_task(
        _background_specialist_setup, specialist.id, db.__class__
    )

    return success({
        "id": specialist.id,
        "name": specialist.name,
        "status": specialist.status,
        "message": f"✅ بدأ إنشاء النموذج '{specialist.display_name}'. Core يبحث عن أفضل المعلومات..."
    })


def _background_specialist_setup(specialist_id: int, db_class):
    """تُشغَّل في الخلفية: Core يبحث ويضبط النموذج"""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        specialist = db.query(SpecialistModel).filter(
            SpecialistModel.id == specialist_id
        ).first()
        if not specialist:
            return

        # البحث عن معلومات التخصص
        from app.services.web.searxng_client import WebIntelligence
        from app.core.intelligence.tool_executor import ToolExecutor

        web = WebIntelligence(db=db)
        knowledge = web.search_for_specialist(specialist.specialization)

        # بناء system prompt
        executor = ToolExecutor(db=db)
        system_prompt = executor._build_specialist_prompt(
            specialist.specialization,
            specialist.display_name,
            knowledge.get("knowledge_base", "")
        )

        specialist.system_prompt = system_prompt
        specialist.training_data_sources = knowledge.get("sources", [])
        specialist.status = "active"
        db.commit()

    except Exception as e:
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

    return success({
        "id": spec.id,
        "name": spec.name,
        "display_name": spec.display_name,
        "display_name_ar": spec.display_name_ar,
        "specialization": spec.specialization,
        "description": spec.description,
        "status": spec.status,
        "base_model": spec.base_model,
        "system_prompt": spec.system_prompt,
        "config_json": spec.config_json,
        "api_endpoint": spec.api_endpoint,
        "is_public_api": spec.is_public_api,
        "total_requests": spec.total_requests,
        "success_rate": spec.success_rate,
        "training_data_sources": spec.training_data_sources,
        "last_trained_at": spec.last_trained_at.isoformat() if spec.last_trained_at else None,
        "created_at": spec.created_at.isoformat(),
    })


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
    for k, v in data.items():
        setattr(spec, k, v)

    db.commit()
    db.refresh(spec)
    return success({"id": spec.id, "status": spec.status, "updated": True})


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

    background_tasks.add_task(_background_specialist_setup, specialist_id, None)

    return success({
        "message": f"بدأ Core في تحديث '{spec.display_name}' بأحدث المعلومات من الإنترنت",
        "session_id": session.id
    })
