"""
Content Sync Webhook
مسار واحد ثابت يستقبله باك إند المستخدمين لإرسال/تحديث محتوى تعليمي.
محمي بـ INTERNAL_API_KEY (مفتاح نظام واحد، منفصل عن مفاتيح النماذج الفردية)
يُسلَّم من الأدمن لباك إند المستخدمين عبر اللوحة.

upsert: إذا external_content_id موجود مسبقاً يُحدَّث، وإلا يُنشأ جديداً.
"""
import hmac
import json
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any
from datetime import datetime

from app.db.session import get_db
from app.core.config import settings
from app.core.responses import success, AppError, ErrorCodes
from app.core.rate_limit import limiter, DEFAULT_RATE_LIMIT
from app.models.education import SyncedContent
from app.services.education.content_normalizer import normalize_to_chunks

router = APIRouter(prefix="/specialist/content", tags=["Public - Content Sync"])


class ContentSyncRequest(BaseModel):
    content_id: str
    title: str | None = None
    payload: dict[str, Any]   # البنية الخام: {"مقدمة": "...", "أهداف": "...", "دروس": [...]}


def _verify_internal_key(x_internal_key: str = Header(default=None, alias="X-Internal-Key")):
    if not x_internal_key or not hmac.compare_digest(x_internal_key, settings.INTERNAL_API_KEY):
        raise AppError(ErrorCodes.UNAUTHORIZED, "X-Internal-Key غير صحيح أو مفقود", 401)
    return True


@router.post("/sync")
@limiter.limit(DEFAULT_RATE_LIMIT)
def sync_content(
    request: Request,
    payload: ContentSyncRequest,
    db: Session = Depends(get_db),
    _verified: bool = Depends(_verify_internal_key),
):
    """
    يستقبله باك إند المستخدمين عند إنشاء أو تحديث محتوى دورة/فصل.
    نفس content_id يُستخدَم للتحديث لاحقاً (upsert كامل).
    """
    chunks = normalize_to_chunks(payload.payload)

    if not chunks:
        raise AppError(ErrorCodes.VALIDATION_ERROR,
                       "لم يتم العثور على أي محتوى قابل للفهرسة في payload المُرسَل")

    existing = db.query(SyncedContent).filter(
        SyncedContent.external_content_id == payload.content_id
    ).first()

    if existing:
        existing.title = payload.title or existing.title
        existing.raw_payload = payload.payload
        existing.chunks_json = chunks
        existing.synced_at = datetime.utcnow()
        db.commit()
        return success({
            "content_id": payload.content_id,
            "action": "updated",
            "sections_count": len(chunks),
            "message": f"✅ تم تحديث المحتوى — {len(chunks)} قسم مُفهرَس"
        })

    record = SyncedContent(
        external_content_id=payload.content_id,
        title=payload.title,
        raw_payload=payload.payload,
        chunks_json=chunks,
    )
    db.add(record)
    db.commit()

    return success({
        "content_id": payload.content_id,
        "action": "created",
        "sections_count": len(chunks),
        "message": f"✅ تم استلام وفهرسة المحتوى — {len(chunks)} قسم"
    })


@router.delete("/sync/{content_id}")
@limiter.limit(DEFAULT_RATE_LIMIT)
def delete_synced_content(
    request: Request,
    content_id: str,
    db: Session = Depends(get_db),
    _verified: bool = Depends(_verify_internal_key),
):
    """لحذف محتوى تم حذفه من باك إند المستخدمين"""
    record = db.query(SyncedContent).filter(
        SyncedContent.external_content_id == content_id
    ).first()
    if not record:
        raise AppError(ErrorCodes.NOT_FOUND, "المحتوى غير موجود", 404)
    db.delete(record)
    db.commit()
    return success({"deleted": True, "content_id": content_id})
