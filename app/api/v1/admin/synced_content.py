"""
Admin API لعرض وإدارة المحتوى المُزامَن من باك إند المستخدمين.
الكتابة الفعلية للمحتوى تتم عبر webhook content_sync.py.
PATCH colors يسمح للأدمن بضبط لوحة ألوان الكورس.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.models.education import SyncedContent, StudentQuestion
from app.utils.listing import ListParams, apply_sort, apply_pagination

router = APIRouter(prefix="/admin/synced-content", tags=["Admin - Synced Content"])


class UpdateColorsRequest(BaseModel):
    color_palette: Optional[str] = None  # "#1E3A5F,#D4A017,#FFFFFF" أو null للحذف


@router.get("")
def list_synced_content(
    params: ListParams = Depends(),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    query = db.query(SyncedContent)
    if params.search:
        query = query.filter(
            SyncedContent.external_content_id.ilike(f"%{params.search}%") |
            SyncedContent.title.ilike(f"%{params.search}%")
        )
    query = apply_sort(query, SyncedContent, params.sort or "-synced_at", default_field="id")
    items, total = apply_pagination(query, params)

    return paginated([{
        "id": c.id,
        "external_content_id": c.external_content_id,
        "title": c.title,
        "color_palette": c.color_palette,
        "sections_count": len(c.chunks_json or []),
        "synced_at": c.synced_at.isoformat(),
        "created_at": c.created_at.isoformat(),
    } for c in items], params.page, params.limit, total)


@router.get("/{content_id}")
def get_synced_content(
    content_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin)
):
    content = db.query(SyncedContent).filter(
        SyncedContent.external_content_id == content_id
    ).first()
    if not content:
        raise AppError(ErrorCodes.NOT_FOUND, "المحتوى غير موجود", 404)

    questions_count = db.query(StudentQuestion).filter(
        StudentQuestion.external_content_id == content_id
    ).count()

    return success({
        "external_content_id": content.external_content_id,
        "title": content.title,
        "color_palette": content.color_palette,
        "chunks": content.chunks_json,
        "questions_asked": questions_count,
        "synced_at": content.synced_at.isoformat(),
    })


@router.patch("/{content_id}/colors")
def update_colors(
    content_id: str,
    payload: UpdateColorsRequest,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """يُحدِّث لوحة ألوان الكورس — تُستخدَم في توليد الصور والإنفوجرافيك."""
    content = db.query(SyncedContent).filter(
        SyncedContent.external_content_id == content_id
    ).first()
    if not content:
        raise AppError(ErrorCodes.NOT_FOUND, "المحتوى غير موجود", 404)
    content.color_palette = payload.color_palette
    db.commit()
    return success({
        "external_content_id": content_id,
        "color_palette": content.color_palette,
    })
