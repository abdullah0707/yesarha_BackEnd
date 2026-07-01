"""
Bundle Chat API — الطبقة الذكية للحزمة.

يستقبل طلباً واحداً بمفتاح حزمة، يُحلّله عبر Orchestrator (qwen3:8b)،
يُوجّهه لأكثر من متخصص إن لزم، يُنفّذهم بالتوازي، ويُجمّع الردود
في إجابة واحدة متكاملة عبر merge ذكي — أو يستريم مباشرة في حالة متخصص واحد.

المدخل: X-API-Key (bundle) + message + optional content_id
المخرج: رد موحّد + معلومات الـ routing
"""
import json
import time
import asyncio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db.session import get_db
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.core.responses import success
from app.core.prompts import build_system_prompt
from app.models.specialist import SpecialistBundle, SpecialistModel, ModelPerformanceLog
from app.services.ollama_client import OllamaClient
from app.core.intelligence.async_bridge import sync_gen_to_async
from app.core.intelligence.api_keys import get_bundle_by_api_key

router = APIRouter(prefix="/specialist/bundle", tags=["Public - Bundle API"])


# ── Orchestrator helpers ───────────────────────────────────────────────────────

def _get_client() -> OllamaClient:
    """يستخدم URL من runtime_cfg تلقائياً"""
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
أنت وكيل توجيه ذكي. مهمتك تحديد أي النماذج المتخصصة يجب أن تتعامل مع طلب المستخدم.

النماذج المتاحة:
{specialists_list}

رسالة المستخدم:
{message}

القواعد:
- أجب فقط بـ JSON صحيح — لا تكتب أي نص آخر
- اختر نموذجاً واحداً إن كان الطلب بسيطاً
- اختر أكثر من نموذج إن كان الطلب يحتاج تخصصات مختلفة
- إذا كان الطلب يحتوي مهام مختلفة لكل نموذج، ضعها في tasks
- اختر فقط من القائمة المتاحة

الصيغة المطلوبة:
{{"route": ["specialization1"], "reason": "سبب الاختيار"}}
أو عند التوجيه لأكثر من نموذج مع مهام مختلفة:
{{"route": ["spec1", "spec2"], "tasks": {{"spec1": "المهمة الأولى", "spec2": "المهمة الثانية"}}, "reason": "سبب الاختيار"}}
"""


def _route_request(message: str, specialists: list[SpecialistModel]) -> dict:
    """
    يستخدم Core model لتحليل الطلب وتحديد المتخصصين المناسبين.
    يُرجع: {"route": ["education"], "tasks": {...}, "reason": "..."}
    """
    if len(specialists) == 1:
        return {"route": [specialists[0].specialization], "reason": "نموذج واحد متاح"}

    specs_list = "\n".join(
        f"- {s.specialization}: {s.display_name} — {s.description or s.display_name}"
        for s in specialists
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

        # استخرج JSON — حتى لو فيه نص إضافي
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            # تحقق أن route موجودة وصحيحة
            if "route" in parsed and isinstance(parsed["route"], list):
                return parsed
    except Exception:
        pass

    # Fallback: اختر المتخصص الأول
    return {"route": [specialists[0].specialization], "reason": "fallback — فشل التحليل"}


# ── Smart Merge ────────────────────────────────────────────────────────────────

_MERGE_PROMPT = """\
أنت مساعد ذكي. لديك ردود من عدة نماذج متخصصة على سؤال المستخدم.
مهمتك دمجها في إجابة واحدة متكاملة وسلسة — بدون تكرار وبدون فقدان معلومة مهمة.

سؤال المستخدم:
{message}

ردود النماذج المتخصصة:
{responses}

اكتب إجابة واحدة نهائية متكاملة — لا تذكر أسماء النماذج في ردك.
"""


def _merge_responses(message: str, responses: dict[str, tuple[str, str]]) -> str:
    """
    يدمج ردود المتخصصين في إجابة واحدة عبر qwen3:8b.
    responses: {specialization: (display_name, content)}
    """
    if len(responses) == 1:
        return list(responses.values())[0][1]

    # بناء نص الردود لإرساله للـ merger
    parts = []
    for spec, (display_name, content) in responses.items():
        if content.strip():
            parts.append(f"[{display_name}]:\n{content.strip()}")
    responses_text = "\n\n".join(parts)

    prompt = _MERGE_PROMPT.format(message=message, responses=responses_text)

    try:
        client = _get_client()
        result = client.chat(
            model=_get_core_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 2000},
            timeout=60,
        )
        merged = result.get("content", "").strip()
        if merged:
            return merged
    except Exception:
        pass

    # Fallback: دمج بسيط بالعناوين
    return "\n\n".join(
        f"### {display_name}\n{content}"
        for _, (display_name, content) in responses.items()
        if content.strip()
    )


# ── Specialist Caller ──────────────────────────────────────────────────────────

def _call_specialist(
    specialist: SpecialistModel,
    message: str,
    content_id: Optional[str],
    history: Optional[list[dict]],
    db: Session,
) -> tuple[str, int]:
    """
    يستدعي متخصصاً واحداً ويُرجع (content, response_ms).
    يُعيد ("", 0) عند الفشل بدل إلقاء exception — حتى لا يوقف الباقين.
    """
    try:
        # نموذج التعليم: قراءة المحتوى من DB
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
            messages.extend(history[-6:])   # آخر 6 رسائل فقط لتوفير الـ context window
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

        # تسجيل الأداء
        try:
            specialist.total_requests = (specialist.total_requests or 0) + 1
            db.add(ModelPerformanceLog(
                model_id=specialist.id,
                model_name=specialist.name,
                user_input=message[:500],
                model_output=content_out[:500],
                tokens_input=result.get("tokens_input", 0),
                tokens_output=result.get("tokens_output", 0),
                response_ms=response_ms,
                status="success",
            ))
            db.commit()
        except Exception:
            db.rollback()

        return content_out, response_ms

    except Exception as e:
        # لا نرمي exception — نُرجع فارغاً
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
        return "", 0


# ── Request Schema ─────────────────────────────────────────────────────────────

class BundleAskRequest(BaseModel):
    message: str
    content_id: Optional[str] = None
    history: Optional[list[dict]] = None
    stream: bool = False


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Main Endpoint ──────────────────────────────────────────────────────────────

@router.post("/ask")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def ask_bundle(
    request: Request,
    payload: BundleAskRequest,
    bundle: SpecialistBundle = Depends(get_bundle_by_api_key),
    db: Session = Depends(get_db),
):
    """
    نقطة الدخول الموحّدة للحزمة.
    - Orchestrator يحلّل الطلب → يختار المتخصصين
    - تنفيذ بالتوازي → دمج ذكي بـ qwen3:8b
    - يدعم streaming للحالة الشائعة (متخصص واحد مختار)
    """
    if not bundle.specialist_ids:
        return success({"answer": "لا يوجد نماذج مرتبطة بهذه الحزمة", "specialists_used": []})

    # جلب النماذج النشطة في الحزمة
    specialists = db.query(SpecialistModel).filter(
        SpecialistModel.id.in_(bundle.specialist_ids),
        SpecialistModel.status == "active",
    ).all()

    if not specialists:
        return success({"answer": "لا يوجد نماذج نشطة في هذه الحزمة", "specialists_used": []})

    start_total = time.perf_counter()

    # ── Orchestrator: تحديد المتخصصين ──
    loop = asyncio.get_event_loop()

    if bundle.use_orchestrator and len(specialists) > 1:
        routing = await loop.run_in_executor(None, _route_request, payload.message, specialists)
    else:
        routing = {"route": [specialists[0].specialization], "reason": "orchestrator معطّل أو متخصص واحد"}

    routed_specs = routing.get("route", [specialists[0].specialization])
    sub_tasks = routing.get("tasks", {})
    routing_reason = routing.get("reason", "")

    # فلترة النماذج المختارة
    selected = [s for s in specialists if s.specialization in routed_specs]
    if not selected:
        selected = specialists[:1]

    # ── Streaming: مسار سريع لمتخصص واحد ──
    if payload.stream and len(selected) == 1:
        return StreamingResponse(
            _stream_single(selected[0], payload, db, routing, start_total),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── تنفيذ بالتوازي ──
    results: dict[str, tuple[str, int]] = {}

    async def _run(spec: SpecialistModel):
        task_msg = sub_tasks.get(spec.specialization, payload.message)
        res = await loop.run_in_executor(
            None, _call_specialist,
            spec, task_msg, payload.content_id, payload.history, db
        )
        results[spec.specialization] = (spec.display_name, res[0])

    await asyncio.gather(*[_run(s) for s in selected])

    # فلتر الفاشلين
    valid_results = {k: v for k, v in results.items() if v[1].strip()}

    # ── Merge الذكي ──
    if not valid_results:
        final_answer = "لم يتمكن أي نموذج من الرد على طلبك."
    elif len(valid_results) == 1:
        final_answer = list(valid_results.values())[0][1]
    else:
        # ندمج الردود عبر qwen3:8b
        final_answer = await loop.run_in_executor(None, _merge_responses, payload.message, valid_results)

    # تحديث إحصائيات الحزمة
    try:
        bundle.total_requests = (bundle.total_requests or 0) + 1
        db.commit()
    except Exception:
        db.rollback()

    total_ms = int((time.perf_counter() - start_total) * 1000)
    failed = [s.specialization for s in selected if s.specialization not in valid_results]

    return success({
        "answer": final_answer,
        "specialists_used": list(valid_results.keys()),
        "specialists_detail": [
            {"specialization": s.specialization, "display_name": s.display_name}
            for s in selected
        ],
        "routing": {
            "decision": routed_specs,
            "reason": routing_reason,
            "failed": failed,
        },
        "response_ms": total_ms,
    })


# ── Streaming (single specialist) ─────────────────────────────────────────────

async def _stream_single(
    specialist: SpecialistModel,
    payload: BundleAskRequest,
    db: Session,
    routing: dict,
    start_total: float,
):
    """SSE streaming لحالة متخصص واحد — أسرع وأكثر تفاعلية"""
    yield _sse({
        "type": "routing",
        "specialist": specialist.specialization,
        "display_name": specialist.display_name,
        "reason": routing.get("reason", ""),
    })

    # بناء الـ system prompt + context
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
                    "response_ms": response_ms,
                    "tokens_input": chunk.get("tokens_input", 0),
                    "tokens_output": chunk.get("tokens_output", 0),
                })
                yield "data: [DONE]\n\n"

                # تسجيل
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
