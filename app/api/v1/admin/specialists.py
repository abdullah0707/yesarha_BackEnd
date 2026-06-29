"""
Admin API للنماذج المتخصصة
إدارة كاملة من لوحة التحكم — إنشاء، Pull تلقائي للموديل، تفعيل API Key، مراقبة الأداء
"""
import json
import time
from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.models.user import Admin
from app.models.specialist import SpecialistModel, ModelPerformanceLog, TrainingSession, CoreTask
from app.utils.listing import ListParams, apply_sort, apply_pagination
from app.core.intelligence.specializations import (
    VALID_SPECIALIZATIONS, SPECIALIZATIONS,
    get_base_model, get_vram_required, is_voice_specialist
)
from app.core.intelligence.api_keys import generate_api_key
from app.core.config import settings

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
    cfg = m.config_json or {}
    data = {
        "id": m.id,
        "name": m.name,
        "display_name": m.display_name,
        "display_name_ar": m.display_name_ar,
        "specialization": m.specialization,
        "status": m.status,
        "base_model": m.base_model,
        "ollama_model_name": m.ollama_model_name,
        "api_endpoint": m.api_endpoint,
        "is_public_api": m.is_public_api,
        "has_api_key": bool(m.api_key),
        "total_requests": m.total_requests,
        "success_rate": m.success_rate,
        "vram_required_gb": m.vram_required_gb,
        "uses_external_content": m.uses_external_content,
        "content_source_url": m.content_source_url,
        "has_content_source_key": bool(m.content_source_api_key),
        # تقدم الإعداد — مخزون في config_json لتجنب migration
        "setup_progress": cfg.get("setup_progress", 100 if m.status == "active" else 0),
        "setup_log":      cfg.get("setup_log", ""),
        "setup_status":   cfg.get("setup_status", m.status),
        "created_at": m.created_at.isoformat(),
    }
    if include_key:
        data["api_key"] = m.api_key
        data["content_source_api_key"] = m.content_source_api_key
    return data


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _cfg_update(specialist: SpecialistModel, db, progress: int, log: str, status: str):
    """يحدّث setup progress في config_json ويحفظ فوراً"""
    try:
        cfg = dict(specialist.config_json or {})
        cfg["setup_progress"] = progress
        cfg["setup_log"] = log
        cfg["setup_status"] = status
        specialist.config_json = cfg
        specialist.status = status
        db.commit()
    except Exception:
        pass


# ── Specializations ───────────────────────────────────────────────

@router.get("/specializations")
def list_specializations(_admin=Depends(get_current_admin)):
    return success([
        {
            "key": key,
            "label_ar": v["label_ar"],
            "label_en": v["label_en"],
            "base_model": v["base_model"],
            "vram_gb": v["vram_gb"],
        }
        for key, v in SPECIALIZATIONS.items()
    ])


# ── List ──────────────────────────────────────────────────────────

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

    # تولّد API Key تلقائياً لأي نموذج active بدون key (حالات قديمة)
    needs_commit = False
    for m in items:
        if m.status == "active" and not m.api_key:
            m.api_key = generate_api_key(m.specialization)
            needs_commit = True
    if needs_commit:
        db.commit()

    return paginated([_serialize(m) for m in items], params.page, params.limit, total)


# ── Create ────────────────────────────────────────────────────────

@router.post("")
def create_specialist(
    payload: CreateSpecialistRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin)
):
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
        api_endpoint=f"/specialist/{payload.name.replace('yesarha-', '')}",
        is_public_api=True,
        created_by_core=False,
        uses_external_content=payload.uses_external_content,
        content_source_url=payload.content_source_url,
        content_source_api_key=payload.content_source_api_key,
        config_json={
            "setup_progress": 0,
            "setup_log": "⏳ بدأ الإعداد...",
            "setup_status": "creating"
        }
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
        "base_model": specialist.base_model,
        "message": f"✅ بدأ إنشاء '{specialist.display_name}'. Core يبحث ويُحمّل الموديل تلقائياً..."
    })


# ── Background Setup (القلب: Pull + بحث + Prompt + API Key) ──────

def _background_specialist_setup(specialist_id: int):
    """
    5 خطوات تُشغَّل في الخلفية:
    1. بحث إنترنت عن معرفة التخصص
    2. بناء System Prompt من المعلومات المجمعة
    3. فحص الموديل — Pull إن لم يكن موجوداً على القرص
    4. Warm-up (تحميل في VRAM)
    5. توليد API Key وتفعيل النموذج
    كل خطوة تُحفظ في config_json ليراها الفرونت عبر /setup-status
    """
    from app.db.session import SessionLocal
    from app.services.web.searxng_client import WebIntelligence
    from app.core.intelligence.tool_executor import ToolExecutor
    from app.services.models.model_manager import model_manager

    db = SessionLocal()
    specialist = None

    try:
        specialist = db.query(SpecialistModel).filter(
            SpecialistModel.id == specialist_id
        ).first()
        if not specialist:
            return

        base_model = specialist.base_model

        # ── الخطوة 1: بحث الإنترنت ──
        _cfg_update(specialist, db, 5, "🔍 Core يبحث على الإنترنت عن أفضل ممارسات التخصص...", "training")
        web = WebIntelligence(db=db)
        knowledge = web.search_for_specialist(specialist.specialization)
        sources_count = len(knowledge.get("sources", []))
        _cfg_update(specialist, db, 20, f"✅ جُمعت {knowledge.get('results_count', 0)} نتيجة من {sources_count} مصدر", "training")

        # ── الخطوة 2: بناء System Prompt ──
        _cfg_update(specialist, db, 25, "🧠 Core يبني System Prompt متخصص...", "training")
        executor = ToolExecutor(db=db)
        system_prompt = executor._build_specialist_prompt(
            specialist.specialization,
            specialist.display_name,
            knowledge.get("knowledge_base", "")
        )
        specialist.system_prompt = system_prompt
        specialist.training_data_sources = knowledge.get("sources", [])
        db.commit()
        _cfg_update(specialist, db, 35, "✅ System Prompt جاهز", "training")

        # ── الخطوة 3: اختيار النموذج المناسب بناءً على VRAM + تحميله ──
        from app.core.intelligence.specializations import get_fallback_model, is_voice_specialist

        # نموذج الصوت له معالجة خاصة (مرحلة 3)
        if is_voice_specialist(specialist.specialization):
            _cfg_update(specialist, db, 75, "🎙️ نموذج الصوت يحتاج إعداداً خاصاً (Whisper + XTTS) — يُستخدم mistral مؤقتاً", "downloading")
            base_model = get_fallback_model(specialist.specialization)
            specialist.base_model = base_model
            db.commit()

        vram_free = model_manager.get_vram_usage_gb()
        vram_available = settings.VRAM_TOTAL_GB - vram_free
        final_model = base_model

        # إذا لم يكفِ VRAM للنموذج الأساسي — جرب الـ fallback
        if vram_available < specialist.vram_required_gb:
            fallback = get_fallback_model(specialist.specialization)
            if fallback != base_model:
                _cfg_update(specialist, db, 38,
                    f"⚠️ VRAM المتاح {vram_available:.1f}GB أقل من المطلوب {specialist.vram_required_gb}GB — سيُستخدم {fallback}",
                    "downloading")
                final_model = fallback
                specialist.base_model = final_model
                db.commit()

        is_downloaded = model_manager.is_model_downloaded(final_model)
        if is_downloaded:
            _cfg_update(specialist, db, 75, f"✅ الموديل '{final_model}' موجود على القرص", "downloading")
        else:
            _cfg_update(specialist, db, 40, f"📥 بدأ تحميل '{final_model}' من Ollama...", "downloading")
            pull_ok = _pull_with_progress(final_model, specialist_id, db)
            if not pull_ok:
                _cfg_update(specialist, db, 40,
                    f"❌ فشل تحميل '{final_model}' — تحقق من اتصال الإنترنت أو توفر الموديل",
                    "error")
                specialist.status = "error"
                db.commit()
                return
            _cfg_update(specialist, db, 75, f"✅ تم تحميل '{final_model}' بنجاح", "downloading")

        # ── الخطوة 4: Warm-up ──
        _cfg_update(specialist, db, 80, f"🔥 تهيئة '{base_model}' في الذاكرة...", "downloading")
        model_manager.ensure_model_loaded(base_model, reserve_core=True)
        specialist.ollama_model_name = base_model
        db.commit()
        _cfg_update(specialist, db, 90, f"✅ '{base_model}' جاهز", "downloading")

        # ── الخطوة 5: API Key + تفعيل ──
        if not specialist.api_key:
            specialist.api_key = generate_api_key(specialist.specialization)
        specialist.status = "active"
        cfg = dict(specialist.config_json or {})
        cfg["setup_progress"] = 100
        cfg["setup_log"] = f"🎉 النموذج '{specialist.display_name}' نشط وجاهز!"
        cfg["setup_status"] = "active"
        specialist.config_json = cfg
        db.commit()

    except Exception as e:
        if specialist:
            cfg = dict(specialist.config_json or {})
            cfg["setup_progress"] = 0
            cfg["setup_log"] = f"❌ خطأ: {str(e)[:200]}"
            cfg["setup_status"] = "error"
            specialist.config_json = cfg
            specialist.status = "error"
            db.commit()
    finally:
        db.close()


def _pull_with_progress(model_name: str, specialist_id: int, db) -> bool:
    """Pull الموديل مع تحديث التقدم كل 10 ثوانٍ — يعمل محلياً وعلى السيرفر السحابي"""
    import requests as req

    specialist = db.query(SpecialistModel).filter(
        SpecialistModel.id == specialist_id
    ).first()
    if not specialist:
        return False

    try:
        with req.post(
            f"{settings.OLLAMA_BASE_URL}/api/pull",
            json={"name": model_name, "stream": True},
            stream=True,
            timeout=3600
        ) as resp:
            if not resp.ok:
                return False

            last_update = time.time()
            total = 0
            completed = 0

            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                status_msg = data.get("status", "")
                total = data.get("total", total)
                completed = data.get("completed", completed)

                if time.time() - last_update > 10:
                    progress_pct = 40
                    if total and total > 0:
                        progress_pct = 40 + int((completed / total) * 35)

                    log_msg = f"📥 {status_msg}"
                    if total and completed:
                        gb_done = completed / (1024 ** 3)
                        gb_total = total / (1024 ** 3)
                        log_msg += f" — {gb_done:.1f} / {gb_total:.1f} GB"

                    try:
                        db.refresh(specialist)
                        cfg = dict(specialist.config_json or {})
                        cfg["setup_progress"] = progress_pct
                        cfg["setup_log"] = log_msg
                        cfg["setup_status"] = "downloading"
                        specialist.config_json = cfg
                        db.commit()
                    except Exception:
                        pass

                    last_update = time.time()

                if data.get("status") == "success":
                    return True

        return True
    except Exception:
        return False


# ── Setup Status SSE ──────────────────────────────────────────────

@router.get("/{specialist_id}/setup-status")
def setup_status_stream(
    specialist_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """
    SSE stream — الفرونت يفتح EventSource هنا ويستقبل updates كل ثانيتين.
    يُغلق تلقائياً عند اكتمال الإنشاء أو وقوع خطأ.
    """
    def _generate():
        max_wait = 3600
        interval = 2
        elapsed = 0

        while elapsed < max_wait:
            try:
                db.expire_all()
                spec = db.query(SpecialistModel).filter(
                    SpecialistModel.id == specialist_id
                ).first()

                if not spec:
                    yield _sse({"type": "error", "message": "النموذج غير موجود"})
                    return

                cfg = spec.config_json or {}
                yield _sse({
                    "type": "status_update",
                    "id": spec.id,
                    "name": spec.name,
                    "display_name": spec.display_name,
                    "status": spec.status,
                    "base_model": spec.base_model,
                    "ollama_model_name": spec.ollama_model_name,
                    "has_api_key": bool(spec.api_key),
                    "setup_progress": cfg.get("setup_progress", 0),
                    "setup_log": cfg.get("setup_log", ""),
                    "setup_status": cfg.get("setup_status", spec.status),
                })

                if spec.status in ("active", "error", "inactive"):
                    yield _sse({"type": "done", "final_status": spec.status})
                    return

            except Exception as e:
                yield _sse({"type": "error", "message": str(e)})
                return

            time.sleep(interval)
            elapsed += interval

        yield _sse({"type": "timeout", "message": "انتهت مهلة الانتظار"})

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


# ── CRUD ──────────────────────────────────────────────────────────

@router.get("/{specialist_id}")
def get_specialist(
    specialist_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)

    # إذا كان النموذج نشطاً ولا يوجد API Key — يُولَّد تلقائياً
    if spec.status == "active" and not spec.api_key:
        spec.api_key = generate_api_key(spec.specialization)
        db.commit()

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

    # حذف كل السجلات المرتبطة أولاً (FK constraints)
    db.query(CoreTask).filter(CoreTask.target_model_id == specialist_id).delete()
    db.query(ModelPerformanceLog).filter(ModelPerformanceLog.model_id == specialist_id).delete()
    db.query(TrainingSession).filter(TrainingSession.model_id == specialist_id).delete()
    db.commit()

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


@router.post("/{specialist_id}/retry-setup")
def retry_setup(
    specialist_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    """إعادة محاولة الـ setup للنماذج التي فشلت — يُعيد من الخطوة الأولى"""
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)

    # إعادة ضبط الحالة
    spec.status = "creating"
    cfg = dict(spec.config_json or {})
    cfg["setup_progress"] = 0
    cfg["setup_log"] = "🔄 إعادة المحاولة..."
    cfg["setup_status"] = "creating"
    spec.config_json = cfg
    db.commit()

    background_tasks.add_task(_background_specialist_setup, spec.id)

    return success({
        "message": f"✅ بدأت إعادة إعداد '{spec.display_name}' من جديد",
        "id": spec.id,
        "status": "creating"
    })


@router.post("/{specialist_id}/trigger-training")
def trigger_training(
    specialist_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    spec = db.query(SpecialistModel).filter(SpecialistModel.id == specialist_id).first()
    if not spec:
        raise AppError(ErrorCodes.NOT_FOUND, "النموذج غير موجود", 404)

    session = TrainingSession(model_id=specialist_id, session_type="prompt", status="pending")
    db.add(session)
    db.commit()

    background_tasks.add_task(_background_specialist_setup, specialist_id)
    return success({
        "message": f"بدأ Core في تحديث '{spec.display_name}' بأحدث المعلومات من الإنترنت",
        "session_id": session.id
    })
