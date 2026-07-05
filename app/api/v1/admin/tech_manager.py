"""
Technical Manager API — Ollama-powered autonomous agent  v3.0
يعمل على Ollama self-hosted — لا Groq، لا APIs خارجية مدفوعة

POST /admin/tech-manager/analyze              → SSE stream (thinking/tool/proposal/done)
GET  /admin/tech-manager/status               → حالة الوكيل والنموذج
GET  /admin/tech-manager/patches              → سجل التعديلات المطبقة
GET  /admin/tech-manager/proposals            → اقتراحات بانتظار الموافقة
GET  /admin/tech-manager/proposals/all        → جميع الاقتراحات (pending+approved+rejected)
POST /admin/tech-manager/proposals/{id}/approve  → موافقة وتطبيق جراحي
POST /admin/tech-manager/proposals/{id}/reject   → رفض
POST /admin/tech-manager/proposals/{id}/rollback → تراجع عن تعديل مطبق
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from app.core.deps import get_current_admin

router = APIRouter(prefix="/admin/tech-manager", tags=["Admin - Technical Manager"])

PATCHES_DIR = Path("/app/data/tech_manager_patches")


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class AnalyzeRequest(BaseModel):
    problem: str

    @field_validator("problem")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("problem cannot be empty")
        return v.strip()


# ══════════════════════════════════════════════════════════════════════════
# Analyze (Main SSE endpoint)
# ══════════════════════════════════════════════════════════════════════════

@router.post("/analyze")
async def analyze_problem(
    payload: AnalyzeRequest,
    _admin=Depends(get_current_admin),
):
    """
    يُشغّل المدير التقني (Ollama) لتحليل وحل مشكلة تقنية.
    يبثّ SSE: thinking → tool_start → tool_result → proposal → done
    """
    from app.services.tech_manager.ollama_agent import run_tech_manager
    from app.services.runtime_config import runtime_cfg
    from app.core.config import settings

    ollama_url = runtime_cfg.get("ollama_base_url") or settings.OLLAMA_BASE_URL
    model      = runtime_cfg.get_core_model() or settings.CORE_MODEL

    async def stream():
        try:
            async for event in run_tech_manager(payload.problem, ollama_url, model):
                yield _sse(event)
        except Exception as ex:
            yield _sse({"type": "error", "content": str(ex)})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ══════════════════════════════════════════════════════════════════════════
# Status
# ══════════════════════════════════════════════════════════════════════════

@router.get("/status")
def get_status(_admin=Depends(get_current_admin)):
    """حالة المدير التقني: النموذج المستخدم، عدد الاقتراحات، آخر تشغيل."""
    from app.services.runtime_config import runtime_cfg
    from app.core.config import settings
    from app.services.tech_manager.patch_engine import load_proposals

    ollama_url = runtime_cfg.get("ollama_base_url") or settings.OLLAMA_BASE_URL
    model      = runtime_cfg.get_core_model() or settings.CORE_MODEL

    proposals = load_proposals()
    pending   = sum(1 for p in proposals if p.get("status") == "pending")
    approved  = sum(1 for p in proposals if p.get("status") == "approved")

    return {
        "status": "success",
        "data": {
            "configured":  True,
            "provider":    "Ollama (self-hosted)",
            "model":       model,
            "ollama_url":  ollama_url,
            "ready":       True,
            "message":     f"جاهز — النموذج: {model}",
            "proposals":   {"pending": pending, "approved": approved, "total": len(proposals)},
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# Patches History
# ══════════════════════════════════════════════════════════════════════════

@router.get("/patches")
def list_patches(_admin=Depends(get_current_admin)):
    """سجل كل التعديلات الفعلية التي طُبِّقت."""
    log_path = PATCHES_DIR / "patch_history.jsonl"
    if not log_path.exists():
        return {"status": "success", "data": {"patches": [], "total": 0}}

    patches = []
    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        for line in reversed(lines[-100:]):
            try:
                patches.append(json.loads(line))
            except Exception:
                pass
    except Exception:
        pass

    return {"status": "success", "data": {"patches": patches, "total": len(patches)}}


# ══════════════════════════════════════════════════════════════════════════
# Proposals
# ══════════════════════════════════════════════════════════════════════════

@router.get("/proposals")
def list_proposals(_admin=Depends(get_current_admin)):
    """اقتراحات بانتظار الموافقة (pending فقط)."""
    from app.services.tech_manager.patch_engine import load_proposals
    proposals = load_proposals()
    pending   = [p for p in proposals if p.get("status") == "pending"]
    return {
        "status": "success",
        "data":   {"proposals": pending, "total": len(pending)},
    }


@router.get("/proposals/all")
def list_all_proposals(_admin=Depends(get_current_admin)):
    """جميع الاقتراحات (pending + approved + rejected + rolled_back)."""
    from app.services.tech_manager.patch_engine import load_proposals
    proposals = load_proposals()
    return {
        "status": "success",
        "data":   {"proposals": list(reversed(proposals)), "total": len(proposals)},
    }


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str, _admin=Depends(get_current_admin)):
    """
    يوافق على اقتراح ويُطبّقه جراحياً:
    - line_edit: يستبدل old_content بـ new_content (يتحقق أولاً)
    - append: يُضيف في نهاية الملف بأمان
    - env_reminder: يُسجّل فقط، لا تغيير في الملفات
    """
    from app.services.tech_manager.patch_engine import apply_proposal

    try:
        result = apply_proposal(proposal_id)
    except FileNotFoundError as ex:
        raise HTTPException(status_code=404, detail=str(ex))
    except ValueError as ex:
        raise HTTPException(status_code=409, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Failed to apply: {ex}")

    return {"status": "success", "data": result}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, _admin=Depends(get_current_admin)):
    """يرفض اقتراحاً معلقاً."""
    from datetime import datetime
    from app.services.tech_manager.patch_engine import load_proposals, save_proposals

    proposals = load_proposals()
    found = False
    for p in proposals:
        if p["id"] == proposal_id and p.get("status") == "pending":
            p["status"]      = "rejected"
            p["rejected_at"] = datetime.utcnow().isoformat()
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="Proposal not found or already processed")

    save_proposals(proposals)
    return {
        "status": "success",
        "data":   {"message": "الاقتراح رُفض", "proposal_id": proposal_id},
    }


@router.post("/proposals/{proposal_id}/rollback")
def rollback_proposal_endpoint(proposal_id: str, _admin=Depends(get_current_admin)):
    """
    يتراجع عن تعديل مُطبَّق — يستعيد النسخة الاحتياطية.
    يعمل فقط على الاقتراحات بحالة 'approved'.
    """
    from app.services.tech_manager.patch_engine import rollback_proposal

    try:
        result = rollback_proposal(proposal_id)
    except FileNotFoundError as ex:
        raise HTTPException(status_code=404, detail=str(ex))
    except ValueError as ex:
        raise HTTPException(status_code=409, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Rollback failed: {ex}")

    return {"status": "success", "data": result}
