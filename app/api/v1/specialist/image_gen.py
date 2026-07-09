"""
Image Generation API — Admin Testing + Pipeline integration
POST /specialist/image/generate  → توليد صورة عبر Stable Diffusion
GET  /specialist/image/{image_id} → تقديم الصورة المخزنة
GET  /specialist/image/status     → حالة SD service
"""
import re
import time
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_admin
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.core.responses import success, AppError, ErrorCodes
from app.db.session import get_db
from app.models.user import Admin

router = APIRouter(prefix="/specialist/image", tags=["Specialist - Image Generation"])

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_IMAGE_TTL = 24 * 3600


class GenerateRequest(BaseModel):
    arabic_request: str
    context: str = ""
    color_palette: str = ""   # مثال: "blue #1E3A5F, gold #D4A017" — ألوان الدورة
    steps: int = 35
    width: int = 768
    height: int = 512


@router.get("/status")
def sd_status(_admin: Admin = Depends(get_current_admin)):
    """حالة Stable Diffusion service."""
    from app.services.sd_client import sd_health
    health = sd_health(settings.SD_BASE_URL)
    return success({
        "enabled": settings.SD_ENABLED,
        "sd_url": settings.SD_BASE_URL,
        **health,
    })


@router.post("/generate")
@limiter.limit("10/minute")
def generate_image(
    request: Request,
    payload: GenerateRequest,
    db: Session = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    """
    يولّد صورة من طلب عربي.
    إذا كان الطلب متعدد العناصر → إنفوجراف SVG.
    إذا كان مفهوماً واحداً → صورة فنية عبر Stable Diffusion.
    """
    from app.core.prompts import is_infographic_request

    start = time.perf_counter()

    # ── مسار الإنفوجراف ───────────────────────────────────────────────────────
    if is_infographic_request(payload.arabic_request):
        from app.services.infographic_service import generate_infographic
        result = generate_infographic(
            payload.arabic_request,
            payload.context,
            payload.color_palette,
        )
        if result is None:
            raise AppError(ErrorCodes.EXECUTION_FAILED, "فشل توليد الإنفوجراف", 500)
        image_url, image_id = result
        gen_ms = int((time.perf_counter() - start) * 1000)
        return success({
            "image_url": image_url,
            "image_id": image_id,
            "sd_prompt": "(infographic — no SD prompt)",
            "arabic_request": payload.arabic_request,
            "gen_ms": gen_ms,
            "type": "infographic",
        })

    # ── مسار Stable Diffusion ────────────────────────────────────────────────
    if not settings.SD_ENABLED:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "SD service معطّل في الإعدادات", 503)

    from app.services.ollama_client import OllamaClient
    from app.services.runtime_config import runtime_cfg
    from app.core.prompts import build_sd_prompt, get_sd_system_prompt
    from app.services.sd_client import generate_image as sd_generate

    client = OllamaClient()
    model = runtime_cfg.get_core_model()
    result_llm = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": get_sd_system_prompt()},
            {"role": "user", "content": build_sd_prompt(payload.arabic_request, payload.context, payload.color_palette)},
        ],
        options={"temperature": 0.4, "num_predict": 120},
        think=False,
    )
    sd_prompt = result_llm["content"].strip().strip('"').strip()

    img_result = sd_generate(
        prompt=sd_prompt,
        steps=payload.steps,
        width=payload.width,
        height=payload.height,
        sd_url=settings.SD_BASE_URL,
    )
    gen_ms = int((time.perf_counter() - start) * 1000)

    if img_result is None:
        raise AppError(ErrorCodes.EXECUTION_FAILED, "فشل توليد الصورة — تأكد من تشغيل SD service", 503)

    image_url, image_id = img_result
    return success({
        "image_url": image_url,
        "image_id": image_id,
        "sd_prompt": sd_prompt,
        "arabic_request": payload.arabic_request,
        "gen_ms": gen_ms,
        "type": "scene",
    })


@router.get("/{image_id}")
def get_image(image_id: str):
    """يُقدّم الصورة المُولَّدة — متاحة 24 ساعة."""
    if not _UUID_RE.match(image_id):
        raise AppError(ErrorCodes.NOT_FOUND, "معرّف الصورة غير صالح", 404)

    from app.services.sd_client import get_cached_image
    path = get_cached_image(image_id)
    if path is None:
        raise AppError(ErrorCodes.NOT_FOUND, "الصورة غير موجودة أو انتهت صلاحيتها", 404)

    media_type = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": f"max-age={_IMAGE_TTL}"},
    )
