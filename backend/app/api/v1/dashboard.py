from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_permission
from app.models.entities import ActivityEvent, Document, Investigation, InvestigationStatus, User

router = APIRouter()


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    counts = await db.execute(select(
        func.count(Investigation.id),
        func.sum(case((Investigation.status.in_([InvestigationStatus.draft, InvestigationStatus.in_progress]), 1), else_=0)),
        func.sum(case((Investigation.status == InvestigationStatus.in_review, 1), else_=0)),
        func.sum(case((Investigation.status == InvestigationStatus.completed, 1), else_=0)),
    ).where(Investigation.created_by_user_id == user.id))
    total, active, review, completed = counts.one()
    return {
        "total_investigations": total or 0,
        "active_investigations": active or 0,
        "in_review_investigations": review or 0,
        "completed_investigations": completed or 0,
    }


@router.get("/recent-investigations")
async def recent_investigations(limit: int = Query(default=8, ge=1, le=50), db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    count = select(func.count(Document.id)).where(Document.investigation_id == Investigation.id).scalar_subquery()
    rows = (await db.execute(
        select(Investigation, count.label("document_count"))
        .where(Investigation.created_by_user_id == user.id)
        .order_by(Investigation.updated_at.desc())
        .limit(limit)
    )).all()
    return [{
        "id": str(item.id), "case_number": item.case_number, "title": item.title,
        "status": item.status.value, "document_count": doc_count or 0, "updated_at": item.updated_at,
    } for item, doc_count in rows]


@router.get("/activity")
async def activity(limit: int = Query(default=8, ge=1, le=50), db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    rows = (await db.execute(
        select(ActivityEvent, Investigation.case_number)
        .outerjoin(Investigation, ActivityEvent.investigation_id == Investigation.id)
        .where(ActivityEvent.actor_user_id == user.id)
        .order_by(ActivityEvent.created_at.desc())
        .limit(limit)
    )).all()
    return [{
        "id": str(event.id), "event_type": event.event_type, "summary": event.summary,
        "created_at": event.created_at, "case_number": case_number,
    } for event, case_number in rows]
