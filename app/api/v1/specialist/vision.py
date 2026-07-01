"""
Vision Specialist API — نموذج الصور والرسوم البيانية
يُستدعى عبر X-API-Key الخاص بنموذج image/vision.

القدرات:
- توليد خرائط ذهنية وتدفقات ومخططات بصيغة Mermaid (تُعرض كصور في الواجهة)
- تحليل ووصف الصور (عند استخدام llava كـ base_model)
- أي طلب بصري أو تصميمي
"""
import json
import time
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Literal

from app.db.session import get_db
from app.core.config import settings
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.core.responses import success
from app.models.specialist import SpecialistModel, ModelPerformanceLog
from app.services.ollama_client import OllamaClient
from app.core.intelligence.async_bridge import sync_gen_to_async
from app.core.intelligence.api_keys import get_specialist_by_api_key
from app.core.prompts import build_system_prompt

router = APIRouter(prefix="/specialist/vision", tags=["Public - Vision Specialist"])

DEFAULT_VISION_PROMPT = """\
أنت متخصص في إنشاء الرسوم البيانية والخرائط الذهنية والتصورات البصرية من يسرها.
مهمتك تحويل النصوص والأفكار إلى تصورات بصرية واضحة ومنظمة.

قدراتك الأساسية:
1. خرائط ذهنية (Mind Maps) بصيغة Mermaid — لتنظيم المعلومات هرمياً
2. مخططات تدفق (Flowcharts) — لتوضيح العمليات والخطوات
3. مخططات تسلسل (Sequence Diagrams) — لتوضيح التفاعلات
4. مخططات ER/UML — للأنظمة التقنية
5. تحليل ووصف الصور (إذا أُرسلت صورة)

قواعد صارمة للرسوم البيانية:
- استخدم صيغة Mermaid دائماً — تُعرض كصورة تلقائياً في الواجهة
- احتوِ كود Mermaid داخل ```mermaid ... ```
- اجعل الرسم واضحاً، منظماً، ومناسباً للموضوع
- أضف شرحاً موجزاً بعد الكود يوضح محتوى الرسم\
"""

_MINDMAP_HINT = """

للخريطة الذهنية استخدم:
```mermaid
mindmap
  root((الموضوع الرئيسي))
    فرع أول
      نقطة فرعية أ
      نقطة فرعية ب
    فرع ثانٍ
      نقطة فرعية ج
```"""

_FLOWCHART_HINT = """

للمخطط الانسيابي استخدم:
```mermaid
flowchart TD
    A[البداية] --> B{قرار}
    B -->|نعم| C[خطوة 1]
    B -->|لا| D[خطوة 2]
    C --> E[النهاية]
    D --> E
```"""

_DIAGRAM_HINTS: dict[str, str] = {
    "mindmap": _MINDMAP_HINT,
    "flowchart": _FLOWCHART_HINT,
}


class VisionAskRequest(BaseModel):
    message: str
    history: Optional[list[dict]] = None
    diagram_type: Optional[Literal["mindmap", "flowchart", "sequence", "er", "auto"]] = None
    stream: bool = True


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_messages(payload: VisionAskRequest, specialist: SpecialistModel) -> list[dict]:
    base = specialist.system_prompt or DEFAULT_VISION_PROMPT
    hint = _DIAGRAM_HINTS.get(payload.diagram_type or "", "")
    system = build_system_prompt(base + hint)

    messages = [{"role": "system", "content": system}]
    if payload.history:
        messages.extend(payload.history)
    messages.append({"role": "user", "content": payload.message})
    return messages


@router.post("/ask")
@limiter.limit(DEFAULT_RATE_LIMIT)
async def ask_vision(
    request: Request,
    payload: VisionAskRequest,
    specialist: SpecialistModel = Depends(get_specialist_by_api_key),
    db: Session = Depends(get_db),
):
    """
    نقطة الدخول للنموذج البصري.
    يدعم توليد خرائط Mermaid وتحليل الصور عبر X-API-Key.
    """
    messages = _build_messages(payload, specialist)

    if payload.stream:
        return StreamingResponse(
            _stream_vision(messages, payload, specialist, db),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    client = OllamaClient(base_url=settings.OLLAMA_BASE_URL)
    start = time.perf_counter()
    result = client.chat(model=specialist.base_model or settings.CORE_MODEL, messages=messages)
    response_ms = int((time.perf_counter() - start) * 1000)
    _log_and_count(db, specialist, payload.message, result["content"], response_ms)

    return success({
        "answer": result["content"],
        "specialist": specialist.display_name,
        "diagram_type": payload.diagram_type,
        "response_ms": response_ms,
    })


async def _stream_vision(
    messages: list[dict],
    payload: VisionAskRequest,
    specialist: SpecialistModel,
    db: Session,
):
    client = OllamaClient(base_url=settings.OLLAMA_BASE_URL)
    full_response = ""
    start = time.perf_counter()

    async for chunk in sync_gen_to_async(
        client.chat_stream,
        model=specialist.base_model or settings.CORE_MODEL,
        messages=messages,
    ):
        if chunk["type"] == "token":
            full_response += chunk["content"]
            yield _sse(chunk)
        elif chunk["type"] in ("done", "error"):
            yield _sse(chunk)

    response_ms = int((time.perf_counter() - start) * 1000)
    _log_and_count(db, specialist, payload.message, full_response, response_ms)

    yield _sse({"type": "done", "full_response": full_response})
    yield "data: [DONE]\n\n"


def _log_and_count(
    db: Session,
    specialist: SpecialistModel,
    user_input: str,
    output: str,
    response_ms: int,
):
    try:
        specialist.total_requests = (specialist.total_requests or 0) + 1
        db.add(ModelPerformanceLog(
            model_id=specialist.id,
            model_name=specialist.name,
            user_input=user_input[:500],
            model_output=output[:500],
            response_ms=response_ms,
            status="success",
        ))
        db.commit()
    except Exception:
        db.rollback()
