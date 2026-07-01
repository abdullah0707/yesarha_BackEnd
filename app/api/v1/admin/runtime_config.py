"""
Runtime Config API — تعديل إعدادات النظام من لوحة التحكم بدون restart
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.db.session import get_db
from app.core.security import require_admin
from app.services.runtime_config import runtime_cfg, SETTING_DEFINITIONS

router = APIRouter(prefix="/admin/runtime-config", tags=["Runtime Config"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class SettingOut(BaseModel):
    key: str
    value: Optional[str]
    group: str
    label_ar: str
    label_en: str
    description: Optional[str]
    value_type: str
    is_secret: bool
    default: Optional[str]


class SettingPatch(BaseModel):
    value: str


class BulkPatch(BaseModel):
    settings: dict[str, str]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", summary="جلب كل الإعدادات الديناميكية")
def list_settings(admin=Depends(require_admin)):
    all_defs = runtime_cfg.get_all_definitions()
    # إخفاء القيمة الحقيقية للإعدادات السرية
    for item in all_defs:
        if item.get("is_secret") and item.get("value"):
            item["value"] = "••••••••"
    return {"status": "success", "data": all_defs}


@router.get("/group/{group}", summary="جلب إعدادات مجموعة معينة")
def list_by_group(group: str, admin=Depends(require_admin)):
    all_defs = runtime_cfg.get_all_definitions()
    filtered = [d for d in all_defs if d["group"] == group]
    for item in filtered:
        if item.get("is_secret") and item.get("value"):
            item["value"] = "••••••••"
    return {"status": "success", "data": filtered}


@router.patch("/{key}", summary="تعديل إعداد واحد")
def update_setting(key: str, body: SettingPatch, db: Session = Depends(get_db), admin=Depends(require_admin)):
    valid_keys = {d["key"] for d in SETTING_DEFINITIONS}
    if key not in valid_keys:
        raise HTTPException(status_code=404, detail=f"الإعداد '{key}' غير موجود")

    # التحقق من صحة القيمة بحسب النوع
    defn = next(d for d in SETTING_DEFINITIONS if d["key"] == key)
    _validate_value(defn["value_type"], body.value)

    runtime_cfg.set(key, body.value, db)
    return {"status": "success", "data": {"key": key, "value": body.value}}


@router.patch("", summary="تعديل عدة إعدادات دفعة واحدة")
def bulk_update(body: BulkPatch, db: Session = Depends(get_db), admin=Depends(require_admin)):
    valid_keys = {d["key"]: d for d in SETTING_DEFINITIONS}
    errors = []
    for key, value in body.settings.items():
        if key not in valid_keys:
            errors.append(f"المفتاح '{key}' غير موجود")
            continue
        try:
            _validate_value(valid_keys[key]["value_type"], value)
        except ValueError as e:
            errors.append(f"{key}: {e}")

    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    runtime_cfg.set_many(body.settings, db)
    return {"status": "success", "data": {"updated": list(body.settings.keys())}}


@router.post("/reload", summary="إعادة تحميل الكاش من DB")
def reload_cache(db: Session = Depends(get_db), admin=Depends(require_admin)):
    runtime_cfg.reload(db)
    return {"status": "success", "data": {"message": "تم إعادة تحميل الإعدادات من قاعدة البيانات"}}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _validate_value(value_type: str, value: str) -> None:
    if value_type == "int":
        try:
            int(value)
        except ValueError:
            raise ValueError(f"يجب أن تكون قيمة صحيحة (integer)")
    elif value_type == "float":
        try:
            float(value)
        except ValueError:
            raise ValueError(f"يجب أن تكون قيمة عشرية (float)")
    elif value_type == "bool":
        if value.lower() not in ("true", "false", "1", "0"):
            raise ValueError("يجب أن تكون القيمة true أو false")
    elif value_type == "json":
        import json
        try:
            json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("يجب أن تكون القيمة JSON صحيح")
