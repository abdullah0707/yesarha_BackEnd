"""
Stable Diffusion Image Generation Service
يعمل كـ microservice منفصل — يستقبل prompt إنجليزي ويرجع صورة PNG كـ base64
النموذج: stabilityai/stable-diffusion-2-1-base
يستخدم CPU offload لمشاركة VRAM مع Ollama
"""
import base64
import logging
import os
import time
from io import BytesIO

import torch
from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sd_service")

MODEL_ID = os.environ.get("SD_MODEL_ID", "Lykon/dreamshaper-7")
HF_CACHE = os.environ.get("HF_HOME", "/hf_cache")
HF_TOKEN = os.environ.get("HF_TOKEN") or None

app = FastAPI(title="Yesarha SD Service")

_pipe = None
_loading = False


def _load_pipe():
    global _pipe, _loading
    if _pipe is not None:
        return _pipe
    _loading = True
    logger.info(f"Loading SD model: {MODEL_ID}")
    start = time.time()

    pipe = StableDiffusionPipeline.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16,
        cache_dir=HF_CACHE,
        safety_checker=None,
        requires_safety_checker=False,
        token=HF_TOKEN,
    )
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(
        pipe.scheduler.config,
        use_karras_sigmas=True,
        algorithm_type="dpmsolver++",
    )
    # CPU offload فضل — يضع أجزاء النموذج على CPU بين الخطوات
    # يوفر ~2GB VRAM مقارنةً بالتحميل الكامل على GPU
    pipe.enable_model_cpu_offload()

    try:
        pipe.enable_xformers_memory_efficient_attention()
        logger.info("xformers memory-efficient attention enabled")
    except Exception:
        pipe.enable_attention_slicing()
        logger.info("attention_slicing enabled (xformers unavailable)")

    _pipe = pipe
    _loading = False
    logger.info(f"SD model ready in {time.time() - start:.1f}s")
    return pipe


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = (
        "ugly, blurry, low quality, distorted, watermark, text, "
        "bad anatomy, extra limbs, poorly drawn, deformed, duplicate"
    )
    steps: int = 25
    width: int = 512
    height: int = 512
    guidance_scale: float = 7.5
    seed: int = -1


@app.get("/health")
def health():
    return {
        "status": "ready" if _pipe is not None else ("loading" if _loading else "idle"),
        "model": MODEL_ID,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
    }


@app.post("/generate")
def generate(req: GenerateRequest):
    if not req.prompt or not req.prompt.strip():
        return JSONResponse(status_code=400, content={"error": "prompt is required"})

    try:
        pipe = _load_pipe()
        generator = None
        if req.seed >= 0:
            generator = torch.Generator("cpu").manual_seed(req.seed)

        start = time.perf_counter()
        result = pipe(
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
            num_inference_steps=req.steps,
            guidance_scale=req.guidance_scale,
            width=req.width,
            height=req.height,
            generator=generator,
        )
        gen_ms = int((time.perf_counter() - start) * 1000)

        image = result.images[0]
        buf = BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        logger.info(f"Generated image in {gen_ms}ms — prompt: {req.prompt[:60]}")
        return {
            "image_b64": b64,
            "width": req.width,
            "height": req.height,
            "gen_ms": gen_ms,
        }

    except Exception as e:
        logger.error(f"Generation failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# تحميل النموذج في الخلفية عند بدء التشغيل
@app.on_event("startup")
async def startup():
    import threading
    threading.Thread(target=_load_pipe, daemon=True).start()
