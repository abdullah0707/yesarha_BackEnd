"""
Standalone Orchestrator API — /specialist/orchestrate/ask

يختلف عن Bundle في نقطتين:
1. لا يحتاج حزمة مُعرَّفة مسبقاً — يختار من كل النماذج النشطة
2. Auth مزدوج: X-API-Key (bundle) أو Bearer JWT (admin)

الاستخدامات:
- اختبار ذكاء الـ Orchestrator مباشرة من لوحة التحكم (JWT)
- استدعاء خارجي بمفتاح حزمة مع تحديد تخصصات معينة (X-API-Key)
- تكامل بين نماذج بدون بناء حزمة مسبقاً
"""
import json
import time
import asyncio
from fastapi import APIRouter, Depends, Request, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional

from app.db.session import get_db
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.core.responses import success
from app.core.prompts import build_system_prompt
from app.models.specialist import SpecialistModel, SpecialistBundle, ModelPerformanceLog
from app.services.ollama_client import OllamaClient
from app.core.intelligence.async_bridge import sync_gen_to_async

router = APIRouter(prefix="/specialist/orchestrate", tags=["Public - Orchestrator"])


# ── Auth: Bearer JWT or X-API-Key (bundle) ────────────────────────────────────

def _get_orchestrate_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """
    يقبل:
    - X-API-Key: yesk_bundle_xxx → يُرجع {"type": "bundle", "bundle": SpecialistBundle}
    - Authorization: Bearer <jwt>  → يُرجع {"type": "admin", "admin": admin_obj}
    """
    from app.core.responses import AppError, ErrorCodes

    # ── Bundle key
    if x_api_key and x_api_key.startswith("yesk_bundle_"):
        bundle = db.query(SpecialistBundle).filter(
            SpecialistBundle.api_key == x_api_key,
            SpecialistBundle.status == "active",
        ).first()
        if not bundle:
            raise AppError(ErrorCodes.UNAUTHORIZED, "مفتاح الحزمة غير صالح أو غير نشط", 401)
        return {"type": "bundle", "bundle": bundle}

    # ── Admin JWT
    if authorization and authorization.startswith("Bearer "):
        from app.core.security import decode_access_token
        token = authorization.split(" ", 1)[1]
        payload = decode_access_token(token)
        if not payload:
            raise AppError(ErrorCodes.UNAUTHORIZED, "التوكن غير صالح", 401)
        return {"type": "admin", "admin_id": payload.get("sub")}

    raise HTTPException(status_code=401, detail="يجب تقديم X-API-Key (bundle) أو Authorization (Bearer JWT)")


# ── Core helpers ───────────────────────────────────────────────────────────────

def _get_client() -> OllamaClient:
    return OllamaClient()


def _get_core_model() -> str:
    try:
        from app.services.runtime_config import runtime_cfg
        return runtime_cfg.get_core_model()
    except Exception:
        from app.core.config import settings
        return settings.CORE_MODEL


# ── Routing ────────────────────────────────────────────────────────────────────

_ROUTING_PROMPT = """\
أنت وكيل توجيه ذكي. مهمتك تحديد أي النماذج المتخصصة تُعالج طلب المستخدم.

النماذج المتاحة:
{specialists_list}

رسالة المستخدم:
{message}

القواعد:
- أجب فقط بـ JSON صحيح — لا تكتب أي نص آخر قبله أو بعده
- اختر نموذجاً واحداً إن كان الطلب بسيطاً
- اختر أكثر من نموذج فقط إن كان الطلب يحتاج تخصصات مختلفة فعلاً
- إذا كان هناك مهام مختلفة لكل نموذج، ضعها في tasks
- اختر فقط من القائمة المتاحة أعلاه

الصيغة:
{{"route": ["specialization"], "reason": "سبب الاختيار"}}
أو:
{{"route": ["spec1","spec2"], "tasks": {{"spec1": "مهمة 1", "spec2": "مهمة 2"}}, "reason": "سبب"}}
"""


def _route(message: str, candidates: list[SpecialistModel]) -> dict:
    if len(candidates) == 1:
        return {"route": [candidates[0].specialization], "reason": "نموذج واحد متاح"}

    specs_list = "\n".join(
        f"- {s.specialization}: {s.display_name} — {s.description or s.display_name}"
        for s in candidates
    )
    prompt = _ROUTING_PROMPT.format(specialists_list=specs_list, message=message)

    try:
        client = _get_client()
        result = client.chat(
            model=_get_core_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 300},
            timeout=20,
        )
        raw = result.get("content", "")
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            if "route" in parsed and isinstance(parsed["route"], list):
                return parsed
    except Exception:
        pass

    return {"route": [candidates[0].specialization], "reason": "fallback — فشل التحليل"}


# ── Specialist execution ───────────────────────────────────────────────────────

def _call_specialist(
    specialist: SpecialistModel,
    message: str,
    content_id: Optional[str],
    history: Optional[list[dict]],
    db: Session,
) -> tuple[str, int, dict]:
    """
    يُرجع (content, response_ms, stats).
    لا يرمي exception — يُرجع ("", 0, {}) عند الفشل.
    """
    stats = {"tokens_input": 0, "tokens_output": 0}
    try:
        if specialist.specialization == "education" and content_id:
            from app.models.education import SyncedContent
            from app.services.education.retriever import retrieve_relevant_chunks, build_context_from_chunks

            content = db.query(SyncedContent).filter(
                SyncedContent.external_content_id == content_id
            ).first()
            if content:
                chunks = content.chunks_json or []
                relevant = retrieve_relevant_chunks(chunks, message, top_k=3)
                context = build_context_from_chunks(relevant)
                system = build_system_prompt(
                    (specialist.system_prompt or "") + f"\n\n## محتوى الدرس ذو الصلة:\n{context}"
                )
            else:
                system = build_system_prompt(specialist.system_prompt or "")
        else:
            system = build_system_prompt(specialist.system_prompt or "")

        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history[-6:])
        messages.append({"role": "user", "content": message})

        client = _get_client()
        start = time.perf_counter()
        result = client.chat(
            model=specialist.base_model or _get_core_model(),
            messages=messages,
            timeout=120,
        )
        response_ms = int((time.perf_counter() - start) * 1000)
        content_out = result.get("content", "")
        stats["tokens_input"] = result.get("tokens_input", 0)
        stats["tokens_output"] = result.get("tokens_output", 0)

        try:
            specialist.total_requests = (specialist.total_requests or 0) + 1
            db.add(ModelPerformanceLog(
                model_id=specialist.id,
                model_name=specialist.name,
                user_input=message[:500],
                model_output=content_out[:500],
                tokens_input=stats["tokens_input"],
                tokens_output=stats["tokens_output"],
                response_ms=response_ms,
                status="success",
            ))
            db.commit()
        except Exception:
            db.rollback()

        return content_out, response_ms, stats

    except Exception as e:
        try:
            db.add(ModelPerformanceLog(
                model_id=specialist.id,
                model_name=specialist.name,
                user_input=message[:500],
                model_output=f"ERROR: {str(e)[:200]}",
                response_ms=0,
                status="failed",
            ))
            db.commit()
        except Exception:
            db.rollback()
        return "", 0, stats


# ── Smart Merge ────────────────────────────────────────────────────────────────

_MERGE_PROMPT = """\
أنت مساعد ذكي. لديك ردود من عدة نماذج متخصصة على سؤال المستخدم.
ادمجها في إجابة واحدة متكاملة وسلسة بدون تكرار وبدون فقدان معلومة مهمة.

سؤال المستخدم:
{message}

ردود النماذج:
{responses}

اكتب إجابة نهائية واحدة — لا تذكر أسماء النماذج.
"""


def _merge_responses(message: str, responses: dict[str, tuple[str, str]]) -> str:
    if len(responses) == 1:
        return list(responses.values())[0][1]

    parts = [f"[{display_name}]:\n{content.strip()}"
             for _, (display_name, content) in responses.items() if content.strip()]
    responses_text = "\n\n".join(parts)

    try:
        client = _get_client()
        result = client.chat(
            model=_get_core_model(),
            messages=[{"role": "user", "content": _MERGE_PROMPT.format(
                message=message, responses=responses_text
            )}],
            options={"temperature": 0.3, "num_predict": 2000},
            timeout=60,
        )
        merged = result.get("content", "").strip()
        if merged:
            return merged
    except Exception:
        pass

    return "\n\n".join(
        f"### {display_name}\n{content}"
        for _, (display_name, content) in responses.items()
        if content.strip()
    )


# ── Request Schema ─────────────────────────────────────────────────────────────

class OrchestrateRequest(BaseModel):
    message: str
    specialist_names: Optional[list[str]] = Field(
        default=None,
        description="قائمة تخصصات لتقييد الاختيار — فارغ = من كل النشطين"
    )
    content_id: Optional[str] = None
    history: Optional[list[dict]] = None
    stream: bool = False
    use_smart_merge: bool = True  # دمج ذكي عند تعدد المتخصصين


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Main Endpoint ──────────────────────────────────────────────────────────────

@router.post("/ask")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def orchestrate_ask(
    request: Request,
    payload: OrchestrateRequest,
    auth: dict = Depends(_get_orchestrate_auth),
    db: Session = Depends(get_db),
):
    """
    Orchestrator مستقل — يختار المتخصصين تلقائياً من بين النشطين.
    يقبل مفتاح حزمة (X-API-Key) أو JWT أدمن (Bearer).

    - specialist_names فارغ → يختار من جميع النماذج النشطة
    - specialist_names محدد → يقتصر الاختيار عليها
    """
    start_total = time.perf_counter()

    # تحديد مجموعة المرشحين
    query = db.query(SpecialistModel).filter(SpecialistModel.status == "active")

    if auth["type"] == "bundle":
        # Bundle key: قصر على النماذج الموجودة في الحزمة
        bundle: SpecialistBundle = auth["bundle"]
        if bundle.specialist_ids:
            query = query.filter(SpecialistModel.id.in_(bundle.specialist_ids))

    if payload.specialist_names:
        query = query.filter(SpecialistModel.specialization.in_(payload.specialist_names))

    candidates = query.all()

    if not candidates:
        return success({
            "answer": "لا يوجد نماذج نشطة تطابق طلبك",
            "specialists_used": [],
        })

    loop = asyncio.get_event_loop()

    # ── Routing ──
    routing = await loop.run_in_executor(None, _route, payload.message, candidates)

    routed_specs = routing.get("route", [candidates[0].specialization])
    sub_tasks = routing.get("tasks", {})
    routing_reason = routing.get("reason", "")

    selected = [s for s in candidates if s.specialization in routed_specs]
    if not selected:
        selected = candidates[:1]

    # ── Streaming لمتخصص واحد ──
    if payload.stream and len(selected) == 1:
        return StreamingResponse(
            _stream_single(selected[0], payload, db, routing, start_total),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── تنفيذ بالتوازي ──
    results: dict[str, tuple[str, str]] = {}
    total_tokens_in = 0
    total_tokens_out = 0

    async def _run(spec: SpecialistModel):
        nonlocal total_tokens_in, total_tokens_out
        task_msg = sub_tasks.get(spec.specialization, payload.message)
        content, _, stats = await loop.run_in_executor(
            None, _call_specialist,
            spec, task_msg, payload.content_id, payload.history, db
        )
        results[spec.specialization] = (spec.display_name, content)
        total_tokens_in += stats.get("tokens_input", 0)
        total_tokens_out += stats.get("tokens_output", 0)

    await asyncio.gather(*[_run(s) for s in selected])

    # فلتر الفاشلين
    valid = {k: v for k, v in results.items() if v[1].strip()}
    failed = [s.specialization for s in selected if s.specialization not in valid]

    # ── Merge ──
    if not valid:
        final_answer = "لم يتمكن أي نموذج من الرد."
    elif len(valid) == 1 or not payload.use_smart_merge:
        final_answer = "\n\n".join(
            f"### {display_name}\n{content}"
            for display_name, content in valid.values()
            if content.strip()
        ) if len(valid) > 1 else list(valid.values())[0][1]
    else:
        final_answer = await loop.run_in_executor(None, _merge_responses, payload.message, valid)

    total_ms = int((time.perf_counter() - start_total) * 1000)

    return success({
        "answer": final_answer,
        "specialists_used": list(valid.keys()),
        "specialists_detail": [
            {"specialization": s.specialization, "display_name": s.display_name, "model": s.base_model}
            for s in selected
        ],
        "routing": {
            "decision": routed_specs,
            "reason": routing_reason,
            "failed": failed,
        },
        "stats": {
            "response_ms": total_ms,
            "tokens_input": total_tokens_in,
            "tokens_output": total_tokens_out,
            "specialists_count": len(selected),
        },
    })


# ── Streaming (single specialist) ─────────────────────────────────────────────

async def _stream_single(
    specialist: SpecialistModel,
    payload: OrchestrateRequest,
    db: Session,
    routing: dict,
    start_total: float,
):
    yield _sse({
        "type": "routing",
        "specialist": specialist.specialization,
        "display_name": specialist.display_name,
        "model": specialist.base_model,
        "reason": routing.get("reason", ""),
    })

    if specialist.specialization == "education" and payload.content_id:
        from app.models.education import SyncedContent
        from app.services.education.retriever import retrieve_relevant_chunks, build_context_from_chunks

        content = db.query(SyncedContent).filter(
            SyncedContent.external_content_id == payload.content_id
        ).first()
        if content:
            chunks = content.chunks_json or []
            relevant = retrieve_relevant_chunks(chunks, payload.message, top_k=3)
            context = build_context_from_chunks(relevant)
            system = build_system_prompt(
                (specialist.system_prompt or "") + f"\n\n## محتوى الدرس ذو الصلة:\n{context}"
            )
        else:
            system = build_system_prompt(specialist.system_prompt or "")
    else:
        system = build_system_prompt(specialist.system_prompt or "")

    messages = [{"role": "system", "content": system}]
    if payload.history:
        messages.extend(payload.history[-6:])
    messages.append({"role": "user", "content": payload.message})

    client = _get_client()
    full_response = ""

    try:
        async for chunk in sync_gen_to_async(
            client.chat_stream,
            model=specialist.base_model or _get_core_model(),
            messages=messages,
        ):
            if chunk["type"] == "token":
                full_response += chunk["content"]
                yield _sse(chunk)
            elif chunk["type"] == "done":
                response_ms = int((time.perf_counter() - start_total) * 1000)
                yield _sse({
                    "type": "done",
                    "full_response": full_response,
                    "specialist": specialist.specialization,
                    "stats": {
                        "response_ms": response_ms,
                        "tokens_input": chunk.get("tokens_input", 0),
                        "tokens_output": chunk.get("tokens_output", 0),
                    },
                })
                yield "data: [DONE]\n\n"

                try:
                    specialist.total_requests = (specialist.total_requests or 0) + 1
                    db.add(ModelPerformanceLog(
                        model_id=specialist.id,
                        model_name=specialist.name,
                        user_input=payload.message[:500],
                        model_output=full_response[:500],
                        tokens_input=chunk.get("tokens_input", 0),
                        tokens_output=chunk.get("tokens_output", 0),
                        response_ms=response_ms,
                        status="success",
                    ))
                    db.commit()
                except Exception:
                    db.rollback()
                return

            elif chunk["type"] == "error":
                yield _sse({"type": "error", "code": chunk.get("code"), "message": chunk.get("message")})
                yield "data: [DONE]\n\n"
                return

    except Exception as e:
        yield _sse({"type": "error", "code": "STREAM_ERROR", "message": str(e)[:200]})
        yield "data: [DONE]\n\n"


# ── Status endpoint (for dashboard testing) ───────────────────────────────────

@router.get("/status")
async def orchestrator_status(
    auth: dict = Depends(_get_orchestrate_auth),
    db: Session = Depends(get_db),
):
    """معلومات الـ Orchestrator — النماذج النشطة + Core model"""
    active = db.query(SpecialistModel).filter(SpecialistModel.status == "active").all()
    return success({
        "core_model": _get_core_model(),
        "active_specialists": [
            {
                "id": s.id,
                "name": s.name,
                "specialization": s.specialization,
                "display_name": s.display_name,
                "base_model": s.base_model,
            }
            for s in active
        ],
        "total_active": len(active),
    })
