import hmac
from collections.abc import AsyncIterator
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import SessionLocal
from app.models.entities import User, UserRole

ROLE_PERMISSIONS = {
    UserRole.admin: ["users:manage", "investigations:read", "investigations:create", "investigations:update"],
    UserRole.investigator: ["investigations:read", "investigations:create", "investigations:update"],
    UserRole.reviewer: ["investigations:read"],
}


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get("dfi_access")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = decode_access_token(token)
        user = await db.get(User, UUID(payload.get("sub", "")))
    except (jwt.PyJWTError, ValueError, TypeError):
        user = None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def require_permission(permission: str):
    async def dependency(user: User = Depends(current_user)) -> User:
        if permission not in ROLE_PERMISSIONS[user.role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return dependency


def require_csrf(request: Request) -> None:
    cookie = request.cookies.get("dfi_csrf")
    header = request.headers.get("x-csrf-token")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Request verification failed")


def user_response(user: User) -> dict:
    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role.value,
        "permissions": ROLE_PERMISSIONS[user.role],
    }
