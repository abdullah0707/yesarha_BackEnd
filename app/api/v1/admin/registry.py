"""
Model Registry API v1.0 — إدارة نماذج Ollama من لوحة التحكم

GET  /admin/registry/status    → VRAM + نماذج محمّلة الآن
GET  /admin/registry/available → كل النماذج المُنزَّلة في Ollama
POST /admin/registry/pull      → pull نموذج جديد (SSE progress)
POST /admin/registry/unload    → تفريغ نموذج من VRAM
POST /admin/registry/delete    → حذف نموذج من القرص
POST /admin/registry/set-core  → تعيين Core Model
POST /admin/registry/sync      → مزامنة DB مع Ollama يدوياً
"""
import json
import logging

import httpx
import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_admin
from app.db.session import get_db
from app.services.models.model_manager import model_manager
from app.services.runtime_config import runtime_cfg

router = APIRouter(prefix="/admin/registry", tags=["Admin - Model Registry"])
_log = logging.getLogger("yesarha.registry")


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
def registry_status(_admin=Depends(get_current_admin)):
    """VRAM + النماذج المحمّلة الآن + معلومات Core"""
    return {"status": "success", "data": model_manager.get_status()}


# ── Available Models ──────────────────────────────────────────────────────────

@router.get("/available")
def list_available_models(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """كل النماذج المُنزَّلة في Ollama مع metadata (حجم، parameters، quantization)"""
    ollama_url = runtime_cfg.get_ollama_url()
    core_model = runtime_cfg.get_core_model()

    try:
        resp = http_requests.get(f"{ollama_url}/api/tags", timeout=5)
        ollama_models = resp.json().get("models", []) if resp.ok else []
    except Exception:
        ollama_models = []

    try:
        resp = http_requests.get(f"{ollama_url}/api/ps", timeout=5)
        running = {m["name"] for m in resp.json().get("models", [])} if resp.ok else set()
    except Exception:
        running = set()

    result = []
    for m in ollama_models:
        name = m.get("name", "")
        details = m.get("details", {})
        size_bytes = m.get("size", 0)
        result.append({
            "name": name,
            "size_gb": round(size_bytes / (1024 ** 3), 2),
            "parameter_size": details.get("parameter_size", "unknown"),
            "quantization": details.get("quantization_level", "unknown"),
            "family": details.get("family", "unknown"),
            "format": details.get("format", "gguf"),
            "is_loaded": name in running,
            "is_core": name == core_model,
            "modified_at": m.get("modified_at"),
        })

    result.sort(key=lambda x: (not x["is_core"], not x["is_loaded"], x["name"]))

    return {
        "status": "success",
        "data": {
            "models": result,
            "total": len(result),
            "loaded_count": len(running),
            "core_model": core_model,
        },
    }


# ── Pull Model ────────────────────────────────────────────────────────────────

class PullRequest(BaseModel):
    model_name: str


@router.post("/pull")
async def pull_model(
    payload: PullRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """
    يُنزِّل نموذجاً من Ollama Hub ويبثّ التقدم عبر SSE.
    يُسجِّل النموذج في ai_models عند الانتهاء.
    """
    model_name = payload.model_name.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name مطلوب")

    ollama_url = runtime_cfg.get_ollama_url()

    async def _stream():
        yield _sse({"type": "start", "model": model_name,
                    "message": f"جاري تحميل {model_name} من Ollama Hub..."})
        try:
            async with httpx.AsyncClient(timeout=3600) as client:
                async with client.stream(
                    "POST",
                    f"{ollama_url}/api/pull",
                    json={"name": model_name, "stream": True},
                ) as resp:
                    if resp.status_code != 200:
                        yield _sse({"type": "error",
                                    "message": f"Ollama رفض الطلب: HTTP {resp.status_code}"})
                        yield "data: [DONE]\n\n"
                        return

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                            completed = event.get("completed", 0)
                            total = event.get("total", 0)
                            percent = int(completed / total * 100) if total > 0 else 0
                            yield _sse({
                                "type": "progress",
                                "status": event.get("status", ""),
                                "percent": percent,
                                "completed_gb": round(completed / (1024 ** 3), 2) if completed else 0,
                                "total_gb": round(total / (1024 ** 3), 2) if total else 0,
                            })
                        except Exception:
                            pass

            _register_in_db(db, model_name)
            yield _sse({"type": "done", "model": model_name,
                        "message": f"✅ {model_name} جاهز للاستخدام"})

        except Exception as e:
            yield _sse({"type": "error", "message": f"فشل التحميل: {str(e)[:200]}"})

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Unload Model ──────────────────────────────────────────────────────────────

class ModelNameRequest(BaseModel):
    model_name: str


@router.post("/unload")
def unload_model_endpoint(
    payload: ModelNameRequest,
    _admin=Depends(get_current_admin),
):
    """تفريغ نموذج من VRAM — يبقى على القرص ويمكن إعادة تحميله"""
    if payload.model_name == runtime_cfg.get_core_model():
        raise HTTPException(status_code=400, detail="لا يمكن تفريغ Core Model من VRAM")

    ok = model_manager.unload_model(payload.model_name)
    if not ok:
        raise HTTPException(status_code=500, detail="فشل التفريغ — تحقق من Ollama")

    return {
        "status": "success",
        "data": {"message": f"تم تفريغ {payload.model_name} من VRAM", "model": payload.model_name},
    }


# ── Delete Model ──────────────────────────────────────────────────────────────

@router.post("/delete")
def delete_model(
    payload: ModelNameRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """حذف نموذج من القرص — لا يمكن حذف Core Model النشط"""
    model_name = payload.model_name
    if model_name == runtime_cfg.get_core_model():
        raise HTTPException(status_code=400,
                            detail="لا يمكن حذف Core Model النشط — غيّره أولاً ثم احذفه")

    ollama_url = runtime_cfg.get_ollama_url()
    try:
        resp = http_requests.delete(
            f"{ollama_url}/api/delete",
            json={"name": model_name},
            timeout=30,
        )
        if not resp.ok:
            raise HTTPException(status_code=500,
                                detail=f"Ollama رفض الحذف: HTTP {resp.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"فشل الحذف: {str(e)[:200]}")

    from app.models.ai import AIModel
    m = db.query(AIModel).filter(AIModel.name == model_name).first()
    if m:
        m.status = "deleted"
        db.commit()

    _log.info(f"Deleted model from Ollama: {model_name}")
    return {"status": "success", "data": {"message": f"تم حذف {model_name} من القرص"}}


# ── Set Core Model ────────────────────────────────────────────────────────────

class SetCoreRequest(BaseModel):
    model_name: str


@router.post("/set-core")
def set_core_model(
    payload: SetCoreRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """
    تعيين نموذج كـ Core Model — يُطبَّق فوراً على كل الطلبات بدون restart.
    النموذج يجب أن يكون مُنزَّلاً في Ollama.
    """
    model_name = payload.model_name.strip()

    if not model_manager.is_model_downloaded(model_name):
        raise HTTPException(
            status_code=404,
            detail=f"النموذج '{model_name}' غير موجود في Ollama — نزّله أولاً من /registry/pull",
        )

    # تحديث runtime_cfg (يُطبَّق فوراً بدون restart)
    runtime_cfg.set("CORE_MODEL", model_name, db)

    # تحديث AIModel.is_default في DB
    from app.models.ai import AIModel
    for m in db.query(AIModel).all():
        m.is_default = (m.name == model_name)
    _register_in_db(db, model_name)  # يضيفه إن لم يكن مسجّلاً

    _log.info(f"Core Model changed to: {model_name}")
    return {
        "status": "success",
        "data": {
            "message": f"✅ Core Model غُيِّر إلى {model_name} — التغيير فوري",
            "new_core_model": model_name,
        },
    }


# ── Manual Sync ───────────────────────────────────────────────────────────────

@router.post("/sync")
def sync_registry(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """مزامنة يدوية بين ai_models DB وما هو فعلاً في Ollama"""
    result = sync_ollama_to_db(db)
    return {"status": "success", "data": result}


# ── Sync Logic (used at startup + manual) ────────────────────────────────────

def sync_ollama_to_db(db: Session) -> dict:
    """
    يُزامن ai_models DB مع Ollama:
    - نموذج في Ollama لكن غير مسجّل → يُضاف (status=active)
    - نموذج مسجّل وموجود في Ollama  → يُحدَّث (status=active)
    - نموذج مسجّل لكن غير موجود     → status=missing
    """
    from app.models.ai import AIModel

    ollama_url = runtime_cfg.get_ollama_url()
    try:
        resp = http_requests.get(f"{ollama_url}/api/tags", timeout=5)
        if not resp.ok:
            return {"error": f"Ollama returned HTTP {resp.status_code}", "synced": 0}
        ollama_names = {m.get("name", "") for m in resp.json().get("models", [])}
    except Exception as e:
        return {"error": f"Ollama not reachable: {str(e)[:100]}", "synced": 0}

    existing = db.query(AIModel).all()
    registered = {m.name: m for m in existing}

    added = updated = missing = 0

    for model in existing:
        if model.name in ollama_names:
            if model.status != "active":
                model.status = "active"
                updated += 1
        elif model.status not in ("deleted", "missing"):
            model.status = "missing"
            missing += 1

    for name in ollama_names:
        if name not in registered:
            db.add(AIModel(
                name=name,
                status="active",
                type="general",
                endpoint_url=ollama_url,
            ))
            added += 1

    db.commit()
    _log.info(f"Registry sync: {len(ollama_names)} in Ollama, +{added} added, {missing} missing")
    return {
        "ollama_models": len(ollama_names),
        "added": added,
        "updated": updated,
        "missing": missing,
        "synced": len(ollama_names),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _register_in_db(db: Session, model_name: str) -> None:
    """يُسجِّل نموذجاً في ai_models إذا لم يكن موجوداً أو يُفعِّله إن كان موجوداً"""
    from app.models.ai import AIModel

    existing = db.query(AIModel).filter(AIModel.name == model_name).first()
    if not existing:
        db.add(AIModel(
            name=model_name,
            status="active",
            type="general",
            endpoint_url=runtime_cfg.get_ollama_url(),
        ))
    elif existing.status != "active":
        existing.status = "active"
    db.commit()
