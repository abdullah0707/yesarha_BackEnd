"""
Core Advisor — العقل التنفيذي لـ Yesarha Core
يحلل حالة النظام، يكتشف المشاكل، يقترح حلول، وينتظر موافقة الأدمن قبل التنفيذ.
"""
import json
import subprocess
import threading
from datetime import datetime, timedelta
from typing import Optional

import requests as http_requests
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, AppError, ErrorCodes
from app.models.specialist import SpecialistModel, ModelPerformanceLog
from app.services.ollama_client import OllamaClient
from app.services.runtime_config import runtime_cfg
from app.core.intelligence.async_bridge import sync_gen_to_async

router = APIRouter(prefix="/admin/core/advisor", tags=["Admin - Core Advisor"])

_SPEED_OPTIONS = {"temperature": 0.15, "num_predict": 1200}

_ADVISOR_SYSTEM_PROMPT = """\
أنت يسرها كور — العقل التنفيذي والمدير التقني لمنظومة يسرها.
You are Yesarha Core — the executive AI brain and technical manager of the Yesarha system.

مهمتك: تحليل حالة النظام وتقديم تقرير تنفيذي مختصر وواضح.
Your task: analyze the system state and deliver a concise executive report.

قواعد التقرير:
- اكتب بالعربية أساساً مع مصطلحات إنجليزية تقنية حيث اللازم
- ابدأ بأهم مشكلة أولاً (الأشد خطورة)
- لكل مشكلة: وصف الأثر → السبب المحتمل → التوصية
- فرّق واضحاً بين: "يمكن لـ Core تنفيذه تلقائياً" و "يتطلب تدخل الأدمن يدوياً"
- الأسلوب: مختصر، مباشر، تنفيذي — لا حشو ولا تكرار
- لا تتجاوز 450 كلمة
- إذا لا توجد مشاكل: أكد ذلك باختصار وأشِر لمستوى الأداء"""


# ── Data Collection ────────────────────────────────────────────────────────────

def _nvidia_query(field: str) -> Optional[float]:
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return round(float(result.stdout.strip().split("\n")[0].strip()) / 1024, 2)
    except Exception:
        pass
    return None


def _get_ollama_loaded_models() -> list[dict]:
    try:
        url = runtime_cfg.get_ollama_url().rstrip("/") + "/api/ps"
        resp = http_requests.get(url, timeout=3)
        if resp.ok:
            return resp.json().get("models", [])
    except Exception:
        pass
    return []


def _ping(url: str) -> bool:
    try:
        return http_requests.get(url, timeout=2).ok
    except Exception:
        return False


def _collect_state(db: Session) -> dict:
    now = datetime.utcnow()

    vram_free  = _nvidia_query("memory.free")
    vram_total = _nvidia_query("memory.total")
    loaded_models = _get_ollama_loaded_models()

    ollama_url   = runtime_cfg.get_ollama_url()
    searxng_url  = runtime_cfg.get_searxng_url()
    ollama_ok    = _ping(ollama_url.rstrip("/") + "/api/tags")
    searxng_ok   = _ping(searxng_url.rstrip("/") + "/search?q=test&format=json") if searxng_url else False

    all_specs    = db.query(SpecialistModel).all()
    error_models = [m for m in all_specs if m.status == "error"]
    creating     = [m for m in all_specs if m.status == "creating"]
    active       = [m for m in all_specs if m.status == "active"]

    stuck = []
    for m in creating:
        if m.created_at and (now - m.created_at).total_seconds() > 1800:
            stuck.append(m)

    since = now - timedelta(hours=24)
    logs  = db.query(ModelPerformanceLog).filter(ModelPerformanceLog.created_at >= since).all()
    total_24h  = len(logs)
    failed_24h = len([l for l in logs if l.status == "error"])
    fail_rate  = round(failed_24h / total_24h * 100, 1) if total_24h > 0 else 0.0

    model_stats: dict[str, dict] = {}
    for lg in logs:
        n = lg.model_name or "unknown"
        if n not in model_stats:
            model_stats[n] = {"total": 0, "failed": 0}
        model_stats[n]["total"] += 1
        if lg.status == "error":
            model_stats[n]["failed"] += 1

    return {
        "vram_free_gb":    vram_free,
        "vram_total_gb":   vram_total,
        "loaded_models":   loaded_models,
        "ollama_online":   ollama_ok,
        "searxng_online":  searxng_ok,
        "ollama_url":      ollama_url,
        "searxng_url":     searxng_url,
        "total_specs":     len(all_specs),
        "active_specs":    len(active),
        "error_models":    [{"id": m.id, "name": m.name, "display_name": m.display_name} for m in error_models],
        "stuck_models":    [{"id": m.id, "name": m.name, "display_name": m.display_name} for m in stuck],
        "total_24h":       total_24h,
        "failed_24h":      failed_24h,
        "failure_rate_24h": fail_rate,
        "model_stats":     model_stats,
        "core_model":      runtime_cfg.get_core_model(),
        "timestamp":       now.isoformat(),
    }


# ── Issue Detection ────────────────────────────────────────────────────────────

def _detect_issues(state: dict) -> list[dict]:
    issues = []

    if not state["ollama_online"]:
        issues.append({
            "severity": "critical",
            "code": "OLLAMA_OFFLINE",
            "title": "Ollama غير متاح",
            "impact": "لا يمكن معالجة أي طلب AI — كل النماذج متوقفة تماماً",
            "can_auto": False,
            "action": None,
            "manual_steps": [
                "شغّل: docker-compose up -d ollama",
                "أو: docker start yesarha-ollama",
                "للتحقق: curl http://localhost:11434/api/tags",
            ],
        })

    if state["searxng_url"] and not state["searxng_online"]:
        issues.append({
            "severity": "warning",
            "code": "SEARXNG_OFFLINE",
            "title": "SearXNG غير متاح",
            "impact": "البحث على الإنترنت والتدريب بمحتوى جديد معطّل",
            "can_auto": False,
            "action": None,
            "manual_steps": [
                "شغّل: docker-compose up -d searxng",
                "أو: docker start yesarha-searxng",
            ],
        })

    for em in state["error_models"]:
        issues.append({
            "severity": "critical",
            "code": "MODEL_ERROR",
            "title": f"نموذج في حالة خطأ: {em['display_name']}",
            "impact": f"النموذج غير قادر على معالجة أي طلبات",
            "can_auto": True,
            "action": "retry_model_setup",
            "action_params": {"model_id": em["id"], "model_name": em["name"]},
            "fix_description": f"إعادة تشغيل إعداد {em['display_name']} تلقائياً",
        })

    for sm in state["stuck_models"]:
        issues.append({
            "severity": "warning",
            "code": "MODEL_STUCK",
            "title": f"نموذج عالق في الإنشاء +30 دقيقة: {sm['display_name']}",
            "impact": "الإعداد لم يكتمل — محتمل مشكلة في Pull أو الشبكة",
            "can_auto": True,
            "action": "retry_model_setup",
            "action_params": {"model_id": sm["id"], "model_name": sm["name"]},
            "fix_description": f"إعادة تشغيل إعداد {sm['display_name']} بعد العلقة",
        })

    if state["failure_rate_24h"] > 20 and state["total_24h"] >= 5:
        worst_name = max(
            state["model_stats"].items(),
            key=lambda x: x[1]["failed"] / max(x[1]["total"], 1),
            default=("غير محدد", {}),
        )[0]
        issues.append({
            "severity": "warning",
            "code": "HIGH_FAILURE_RATE",
            "title": f"معدل فشل مرتفع {state['failure_rate_24h']}% آخر 24 ساعة",
            "impact": f"أكثر نموذج مشكلة: {worst_name} — يؤثر على تجربة المستخدم",
            "can_auto": False,
            "action": None,
            "manual_steps": [
                "راجع تفاصيل الإخفاقات في صفحة الإحصاء",
                "تحقق من system prompt النموذج المشكل",
                "تأكد من أن Ollama يستجيب بشكل طبيعي",
            ],
        })

    if state["vram_free_gb"] is not None and state["vram_free_gb"] < 2.0:
        running_names = [m.get("name", "") for m in state["loaded_models"][:3]]
        issues.append({
            "severity": "warning",
            "code": "LOW_VRAM",
            "title": f"VRAM منخفض: {state['vram_free_gb']} GB متاح فقط",
            "impact": "لا يمكن إنشاء نماذج جديدة — قد تتباطأ النماذج الحالية",
            "can_auto": False,
            "action": None,
            "manual_steps": [
                f"أوقف بعض النماذج الجارية: {running_names}",
                "من Ollama: ollama stop <model-name>",
                "انتظر 60 ثانية لتحرير VRAM",
            ],
        })

    return issues


def _build_context(state: dict, issues: list[dict]) -> str:
    lines = [
        f"=== تقرير حالة النظام | System State Report ===",
        f"الوقت: {state['timestamp']}",
        "",
        "[GPU / VRAM]",
        (f"- حر: {state['vram_free_gb']} GB | إجمالي: {state['vram_total_gb']} GB"
         if state["vram_free_gb"] is not None
         else "- لا معلومات GPU (بيئة بدون nvidia-smi)"),
        f"- نماذج محملة في Ollama: {len(state['loaded_models'])}",
        "",
        "[الخدمات]",
        f"- Ollama:   {'✓ متاح' if state['ollama_online'] else '✗ غير متاح'}",
        f"- SearXNG:  {'✓ متاح' if state['searxng_online'] else '✗ غير متاح'}",
        "",
        "[النماذج المتخصصة]",
        f"- الإجمالي: {state['total_specs']} | النشطة: {state['active_specs']}",
        f"- أخطاء: {len(state['error_models'])} | عالقة: {len(state['stuck_models'])}",
        "",
        "[أداء آخر 24 ساعة]",
        f"- طلبات: {state['total_24h']} | معدل فشل: {state['failure_rate_24h']}%",
        "",
        f"[المشاكل المكتشفة — {len(issues)}]",
    ]
    for i, iss in enumerate(issues, 1):
        auto = "يمكن لـ Core تنفيذه" if iss["can_auto"] else "يتطلب تدخل الأدمن"
        lines.append(f"{i}. [{iss['severity'].upper()}] {iss['title']}")
        lines.append(f"   الأثر: {iss['impact']}")
        lines.append(f"   التنفيذ: {auto}")
    if not issues:
        lines.append("لا مشاكل — النظام يعمل بشكل سليم.")
    return "\n".join(lines)


# ── SSE Stream ─────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _advisor_stream(db: Session):
    yield _sse({"type": "status", "message": "جاري تحليل حالة النظام..."})

    try:
        state = _collect_state(db)
    except Exception as exc:
        yield _sse({"type": "error", "message": f"فشل جمع البيانات: {str(exc)}"})
        yield "data: [DONE]\n\n"
        return

    issues = _detect_issues(state)

    yield _sse({
        "type": "state",
        "data": {
            "vram_free_gb":       state["vram_free_gb"],
            "vram_total_gb":      state["vram_total_gb"],
            "ollama_online":      state["ollama_online"],
            "searxng_online":     state["searxng_online"],
            "active_specs":       state["active_specs"],
            "total_specs":        state["total_specs"],
            "error_models_count": len(state["error_models"]),
            "stuck_models_count": len(state["stuck_models"]),
            "failure_rate_24h":   state["failure_rate_24h"],
            "total_24h":          state["total_24h"],
            "loaded_ollama":      len(state["loaded_models"]),
        },
    })

    for issue in issues:
        yield _sse({"type": "issue", **issue})

    yield _sse({"type": "status", "message": "Core يكتب التحليل التنفيذي..."})

    client = OllamaClient()
    async for chunk in sync_gen_to_async(
        client.chat_stream,
        model=state["core_model"],
        messages=[
            {"role": "system", "content": _ADVISOR_SYSTEM_PROMPT},
            {"role": "user",   "content": _build_context(state, issues)},
        ],
        options=_SPEED_OPTIONS,
    ):
        if chunk["type"] == "token":
            yield _sse({"type": "token", "content": chunk["content"]})
        elif chunk["type"] == "error":
            yield _sse({"type": "ai_error", "message": chunk.get("message", "")})

    proposals = []
    for i, iss in enumerate(issues):
        if iss.get("can_auto") and iss.get("action"):
            proposals.append({
                "id": f"prop_{i}",
                "title": iss["title"],
                "description": iss.get("fix_description", ""),
                "action_type": iss["action"],
                "action_params": iss.get("action_params", {}),
                "severity": iss["severity"],
            })

    for prop in proposals:
        yield _sse({"type": "proposal", **prop})

    yield _sse({
        "type": "done",
        "issues_count":    len(issues),
        "proposals_count": len(proposals),
    })
    yield "data: [DONE]\n\n"


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/stream")
async def advisor_stream(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """
    يبثّ تحليل Core للنظام عبر SSE.
    المراحل: جمع البيانات → اكتشاف المشاكل → تحليل AI → اقتراح حلول
    """
    return StreamingResponse(
        _advisor_stream(db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ExecuteProposalRequest(BaseModel):
    action_type: str
    action_params: dict = {}


@router.post("/execute")
def execute_proposal(
    payload: ExecuteProposalRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """
    ينفذ اقتراحاً وافق عليه الأدمن.
    يقبل فقط الإجراءات ذات can_auto=True.
    """
    if payload.action_type == "retry_model_setup":
        return _execute_retry_model(payload.action_params, db)

    raise AppError(
        ErrorCodes.VALIDATION_ERROR,
        f"نوع الإجراء غير معتمد للتنفيذ التلقائي: {payload.action_type}",
        400,
    )


def _execute_retry_model(params: dict, db: Session):
    model_id = params.get("model_id")
    if not model_id:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "model_id مطلوب", 400)

    model = db.query(SpecialistModel).filter(SpecialistModel.id == model_id).first()
    if not model:
        raise AppError(ErrorCodes.NOT_FOUND, f"النموذج {model_id} غير موجود", 404)

    cfg = dict(model.config_json or {})
    cfg["setup_progress"] = 0
    cfg["setup_log"]      = "⏳ Core أعاد تشغيل الإعداد بعد موافقة الأدمن..."
    cfg["setup_status"]   = "creating"
    model.config_json = cfg
    model.status      = "creating"
    db.commit()

    def _run():
        from app.api.v1.admin.specialists import _background_specialist_setup
        _background_specialist_setup(model.id)

    threading.Thread(target=_run, daemon=True).start()

    return success({
        "executed":    True,
        "model_id":    model_id,
        "model_name":  model.name,
        "message":     f"تم إعادة تشغيل إعداد '{model.display_name}' — تابع التقدم في صفحة النماذج المتخصصة",
    })


class ResearchRequest(BaseModel):
    specialist_id: int
    topic: str
    specialization: str = "custom"


@router.post("/research")
def research_fresh_content(
    payload: ResearchRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """
    يبحث عن محتوى جديد وحديث لتدريب النموذج.
    يتجاهل الكاش دائماً — محتوى حقيقي جديد فقط.
    """
    from app.services.web.searxng_client import WebIntelligence

    year = datetime.utcnow().year
    queries = [
        f"{payload.topic} {payload.specialization} {year}",
        f"{payload.topic} best practices tutorial {year}",
        f"{payload.specialization} AI latest updates {year}",
    ]

    web = WebIntelligence(db=db)
    all_results: list[dict] = []

    for q in queries:
        try:
            results = web.search(q, max_results=5, use_cache=False)
            all_results.extend(results)
        except Exception:
            pass

    seen: set[str] = set()
    unique = []
    for r in all_results:
        url = r.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(r)

    return success({
        "specialist_id":  payload.specialist_id,
        "topic":          payload.topic,
        "results_count":  len(unique),
        "results":        unique[:12],
        "queries_used":   queries,
        "note":           "محتوى حديث — تم تجاهل الكاش | Fresh content — cache bypassed",
    })
