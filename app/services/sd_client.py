"""
Client للتواصل مع Stable Diffusion microservice.
يحفظ الصور في /app/data/image_cache/ مع TTL 24h.
"""
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("yesarha.sd_client")

_IMAGE_CACHE_DIR = Path("/app/data/image_cache")
_IMAGE_TTL = 24 * 3600
_last_cleanup: float = 0.0
_CLEANUP_INTERVAL = 600


def _cache_dir() -> Path:
    _IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _IMAGE_CACHE_DIR


def _cleanup() -> None:
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    cutoff = now - _IMAGE_TTL
    try:
        for f in list(_cache_dir().glob("*.png")) + list(_cache_dir().glob("*.svg")):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def generate_image(
    prompt: str,
    negative_prompt: str = "",
    steps: int = 35,
    width: int = 768,
    height: int = 512,
    sd_url: str = "http://stable-diffusion:7860",
    timeout: float = 180.0,
) -> Optional[tuple[str, str]]:
    """
    يستدعي SD service ويحفظ الصورة في كاش.
    يرجع (image_url, image_id) أو None عند الفشل.
    """
    try:
        resp = httpx.post(
            f"{sd_url}/generate",
            json={
                "prompt": prompt,
                "negative_prompt": negative_prompt or (
                    "ugly, blurry, low quality, watermark, text, letters, words, "
                    "labels, captions, writing, illegible text, distorted, "
                    "bad anatomy, extra limbs, duplicate, deformed, low resolution"
                ),
                "steps": steps,
                "width": width,
                "height": height,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        import base64
        img_bytes = base64.b64decode(data["image_b64"])

        _cleanup()
        image_id = str(uuid.uuid4())
        (_cache_dir() / f"{image_id}.png").write_bytes(img_bytes)

        url = f"/api/v1/specialist/pipeline/image/{image_id}"
        logger.info(f"SD image saved: {image_id} ({len(img_bytes)//1024}KB)")
        return url, image_id

    except httpx.ConnectError:
        logger.warning("SD service not reachable — image generation skipped")
        return None
    except Exception as e:
        logger.warning(f"SD generation failed: {e}")
        return None


def get_cached_image(image_id: str) -> Optional[Path]:
    """يرجع Path إذا الصورة موجودة ضمن TTL. يدعم PNG و SVG."""
    import re
    if not re.fullmatch(r"[0-9a-f\-]{36}", image_id):
        return None
    for ext in (".png", ".svg"):
        path = _cache_dir() / f"{image_id}{ext}"
        if path.exists():
            if time.time() - path.stat().st_mtime > _IMAGE_TTL:
                path.unlink(missing_ok=True)
                return None
            return path
    return None


def sd_health(sd_url: str = "http://stable-diffusion:7860") -> dict:
    """يتحقق من حالة SD service."""
    try:
        resp = httpx.get(f"{sd_url}/health", timeout=5.0)
        return resp.json()
    except Exception:
        return {"status": "unreachable"}
