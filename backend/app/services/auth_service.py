import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import hash_password, hash_token, new_opaque_token, verify_password
from app.models.entities import PasswordResetToken, RefreshSession, User, UserRole

_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password-used-for-timing-equalization")


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def register_user(db: AsyncSession, full_name: str, email: str, password: str) -> User:
    user = User(full_name=full_name.strip(), email=normalize_email(email), password_hash=hash_password(password), role=UserRole.investigator)
    db.add(user)
    await db.flush()
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    verified = verify_password(password, user.password_hash if user is not None else _DUMMY_PASSWORD_HASH)
    if user is None or not user.is_active or not verified:
        return None
    user.last_login_at = datetime.now(timezone.utc)
    return user


async def issue_refresh_session(db: AsyncSession, user: User, user_agent: str | None) -> str:
    raw_token = new_opaque_token()
    db.add(RefreshSession(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
        user_agent=user_agent[:256] if user_agent else None,
    ))
    return raw_token


async def rotate_refresh_session(db: AsyncSession, raw_token: str, user_agent: str | None) -> tuple[User, str] | None:
    session = await db.scalar(select(RefreshSession).options(selectinload(RefreshSession.user)).where(
        RefreshSession.token_hash == hash_token(raw_token),
    ).with_for_update())
    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or _as_utc(session.expires_at) <= now or session.user is None or not session.user.is_active:
        return None
    session.revoked_at = now
    new_token = await issue_refresh_session(db, session.user, user_agent)
    return session.user, new_token


async def revoke_refresh_session(db: AsyncSession, raw_token: str | None) -> None:
    if raw_token:
        await db.execute(update(RefreshSession).where(
            RefreshSession.token_hash == hash_token(raw_token),
            RefreshSession.revoked_at.is_(None),
        ).values(revoked_at=datetime.now(timezone.utc)))


async def create_password_reset(db: AsyncSession, email: str) -> None:
    user = await db.scalar(select(User).where(User.email == normalize_email(email), User.is_active.is_(True)))
    if user is None:
        return
    now = datetime.now(timezone.utc)
    await db.execute(update(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).values(used_at=now))
    raw = new_opaque_token()
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=now + timedelta(minutes=settings.password_reset_minutes),
    ))
    await db.flush()
    sink_path = Path(settings.email_sink_file)
    sink_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"to": user.email, "subject": "DocForensIQ password reset", "reset_url": f"{os.getenv('FRONTEND_ORIGIN', settings.frontend_origin)}/reset-password?token={raw}"}
    with sink_path.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(record) + "\n")


async def confirm_password_reset(db: AsyncSession, raw: str, password: str) -> bool:
    token = await db.scalar(select(PasswordResetToken).where(
        PasswordResetToken.token_hash == hash_token(raw),
    ).with_for_update())
    now = datetime.now(timezone.utc)
    if token is None or token.used_at is not None or _as_utc(token.expires_at) <= now:
        return False
    user = await db.get(User, token.user_id)
    if user is None:
        return False
    token.used_at = now
    user.password_hash = hash_password(password)
    await db.execute(update(RefreshSession).where(
        RefreshSession.user_id == user.id,
        RefreshSession.revoked_at.is_(None),
    ).values(revoked_at=now))
    return True


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def create_admin(db: AsyncSession, full_name: str, email: str, password: str) -> User:
    normalized_email = normalize_email(email)
    existing = await db.scalar(select(User).where(User.email == normalized_email))
    if existing:
        now = datetime.now(timezone.utc)
        existing.full_name = full_name.strip()
        existing.email = normalized_email
        existing.password_hash = hash_password(password)
        existing.role = UserRole.admin
        existing.is_active = True
        existing.is_verified = True
        await db.execute(update(RefreshSession).where(
            RefreshSession.user_id == existing.id,
            RefreshSession.revoked_at.is_(None),
        ).values(revoked_at=now))
        return existing
    user = User(full_name=full_name.strip(), email=normalized_email, password_hash=hash_password(password), role=UserRole.admin, is_verified=True)
    db.add(user)
    return user
