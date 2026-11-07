import secrets
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_db, require_csrf, user_response
from app.core.config import settings
from app.core.security import create_access_token
from app.models.entities import User
from app.schemas.auth import LoginRequest, RegisterRequest, ResetConfirmRequest, ResetRequest
from app.services.auth_service import authenticate, confirm_password_reset, create_password_reset, issue_refresh_session, register_user, revoke_refresh_session, rotate_refresh_session

router = APIRouter()
_attempts: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def enforce_rate_limit(request: Request, action: str) -> None:
    now = time.monotonic()
    key = (action, request.client.host if request.client else "unknown")
    attempts = _attempts[key]
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
    attempts.append(now)


def set_session_cookies(response: Response, user: User, refresh: str, csrf: str) -> None:
    common = {"secure": settings.cookie_secure, "samesite": "strict"}
    response.set_cookie("dfi_access", create_access_token(str(user.id), user.role.value), httponly=True, max_age=settings.access_token_minutes * 60, path="/api/v1", **common)
    response.set_cookie("dfi_refresh", refresh, httponly=True, max_age=settings.refresh_token_days * 86400, path="/api/v1/auth", **common)
    response.set_cookie("dfi_csrf", csrf, httponly=False, max_age=settings.refresh_token_days * 86400, path="/", **common)


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie("dfi_access", path="/api/v1", samesite="strict", secure=settings.cookie_secure)
    response.delete_cookie("dfi_refresh", path="/api/v1/auth", samesite="strict", secure=settings.cookie_secure)
    response.delete_cookie("dfi_csrf", path="/", samesite="strict", secure=settings.cookie_secure)


@router.post("/register", status_code=201)
async def register(payload: RegisterRequest, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    enforce_rate_limit(request, "register")
    if not settings.self_registration_enabled:
        raise HTTPException(status_code=404, detail="Registration is not available")
    try:
        user = await register_user(db, payload.full_name, str(payload.email), payload.password)
        refresh = await issue_refresh_session(db, user, request.headers.get("user-agent"))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Unable to create account with those details") from None
    set_session_cookies(response, user, refresh, secrets.token_urlsafe(32))
    return user_response(user)


@router.post("/login")
async def login(payload: LoginRequest, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    enforce_rate_limit(request, "login")
    user = await authenticate(db, str(payload.email), payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    refresh = await issue_refresh_session(db, user, request.headers.get("user-agent"))
    await db.commit()
    set_session_cookies(response, user, refresh, secrets.token_urlsafe(32))
    return user_response(user)


@router.post("/logout", status_code=204)
async def logout(response: Response, request: Request, db: AsyncSession = Depends(get_db), _user: User = Depends(current_user)):
    require_csrf(request)
    await revoke_refresh_session(db, request.cookies.get("dfi_refresh"))
    await db.commit()
    clear_session_cookies(response)


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    require_csrf(request)
    raw = request.cookies.get("dfi_refresh")
    if not raw:
        raise HTTPException(status_code=401, detail="Authentication required")
    rotated = await rotate_refresh_session(db, raw, request.headers.get("user-agent"))
    if rotated is None:
        await db.rollback()
        clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Authentication required")
    user, new_refresh = rotated
    await db.commit()
    set_session_cookies(response, user, new_refresh, secrets.token_urlsafe(32))
    return user_response(user)


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return user_response(user)


@router.post("/password-reset/request")
async def password_reset_request(payload: ResetRequest, request: Request, db: AsyncSession = Depends(get_db)):
    enforce_rate_limit(request, "password-reset")
    await create_password_reset(db, str(payload.email))
    await db.commit()
    return {"message": "If an active account matches that email, password reset instructions will be sent."}


@router.post("/password-reset/confirm")
async def password_reset_confirm(payload: ResetConfirmRequest, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    enforce_rate_limit(request, "password-reset-confirm")
    changed = await confirm_password_reset(db, payload.token, payload.password)
    if not changed:
        await db.rollback()
        raise HTTPException(status_code=400, detail="This password reset link is invalid or expired")
    await db.commit()
    clear_session_cookies(response)
    return {"message": "Password reset successfully. Please sign in again."}
