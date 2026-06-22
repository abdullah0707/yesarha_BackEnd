"""
Admin API لعرض حالة المحتوى المُزامَن من باك إند المستخدمين.
للقراءة والمراقبة فقط من اللوحة — الكتابة الفعلية تتم عبر webhook
content_sync.py (الذي يستدعيه باك إند المستخدمين مباشرة، وليس الأدمن).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import get_current_admin
from app.core.responses import success, paginated, AppError, ErrorCodes
from app.models.education import SyncedContent, StudentQuestion
from app.utils.listing import ListParams, apply_sort, apply_pagination

router = APIRouter(prefix="/admin/synced-content", tags=["Admin - Synced Content"])


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
        "chunks": content.chunks_json,
        "questions_asked": questions_count,
        "synced_at": content.synced_at.isoformat(),
    })
