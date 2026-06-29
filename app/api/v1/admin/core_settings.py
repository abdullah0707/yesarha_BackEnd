"""
Core Settings API — تعديل System Prompt وإعدادات Core في runtime
بدون إعادة تشغيل السيرفر
"""
import json
from pathlib import Path
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from app.core.deps import get_current_admin
from app.core.responses import success, AppError, ErrorCodes
from app.core.intelligence.tool_engine import CORE_SYSTEM_PROMPT, CORE_TOOLS

router = APIRouter(prefix="/admin/core-settings", tags=["Admin - Core Settings"])

PROMPT_PATH = Path("data/core_system_prompt.txt")
CONFIG_PATH = Path("data/core_config.json")


def _get_active_prompt() -> str:
    if PROMPT_PATH.exists():
        try:
            c = PROMPT_PATH.read_text(encoding="utf-8").strip()
            if len(c) >= 50:
                return c
        except Exception:
            pass
    return CORE_SYSTEM_PROMPT


def _save_prompt(prompt: str):
    PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_PATH.write_text(prompt, encoding="utf-8")


def _get_config() -> dict:
    defaults = {
        "temperature": 0.7, "top_p": 0.9,
        "max_tokens": 4096, "enable_tools": True,
        "max_tool_iterations": 3,
    }
    if CONFIG_PATH.exists():
        try:
            return {**defaults, **json.loads(CONFIG_PATH.read_text())}
        except Exception:
            pass
    return defaults


def _save_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2))


class UpdatePromptRequest(BaseModel):
    system_prompt: str


class UpdateConfigRequest(BaseModel):
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    enable_tools: Optional[bool] = None
    max_tool_iterations: Optional[int] = None


@router.get("")
def get_core_settings(_admin=Depends(get_current_admin)):
    return success({
        "system_prompt": _get_active_prompt(),
        "default_system_prompt": CORE_SYSTEM_PROMPT,
        "is_customized": PROMPT_PATH.exists(),
        "tools_count": len(CORE_TOOLS),
        "tools": [t["function"]["name"] for t in CORE_TOOLS],
        "config": _get_config(),
    })


@router.put("/prompt")
@router.patch("/prompt")
def update_prompt(payload: UpdatePromptRequest, _admin=Depends(get_current_admin)):
    if len(payload.system_prompt.strip()) < 50:
        raise AppError(ErrorCodes.VALIDATION_ERROR, "System prompt قصير جداً (50 حرف minimum)")
    _save_prompt(payload.system_prompt.strip())
    return success({
        "message": "✅ تم تحديث System Prompt — مُفعَّل فوراً على كل محادثة جديدة",
        "length": len(payload.system_prompt),
        "is_customized": True,
    })


@router.post("/prompt/reset")
def reset_prompt(_admin=Depends(get_current_admin)):
    if PROMPT_PATH.exists():
        PROMPT_PATH.unlink()
    return success({
        "message": "✅ تم إعادة System Prompt للإعداد الافتراضي",
        "system_prompt": CORE_SYSTEM_PROMPT,
        "is_customized": False,
    })


@router.put("/config")
@router.patch("/config")
def update_config(payload: UpdateConfigRequest, _admin=Depends(get_current_admin)):
    current = _get_config()
    current.update(payload.model_dump(exclude_unset=True))
    _save_config(current)
    return success({"message": "✅ تم تحديث إعدادات Core", "config": current})


@router.get("/tools")
def get_tools(_admin=Depends(get_current_admin)):
    return success({
        "total": len(CORE_TOOLS),
        "tools": [{
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "parameters": list(t["function"].get("parameters", {}).get("properties", {}).keys()),
        } for t in CORE_TOOLS]
    })
