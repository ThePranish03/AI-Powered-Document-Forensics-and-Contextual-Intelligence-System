from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_permission, user_response
from app.models.entities import User

router = APIRouter()


@router.get("")
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_permission("users:manage")),
):
    users = (await db.scalars(select(User).order_by(User.created_at.desc()).limit(100))).all()
    return [user_response(user) for user in users]
