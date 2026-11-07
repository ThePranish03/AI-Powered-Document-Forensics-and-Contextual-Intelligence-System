import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.api.v1 import auth as auth_routes
from app.core.config import settings
from app.core.security import hash_password, hash_token, verify_password
from app.main import app
from app.models.entities import PasswordResetToken, RefreshSession, User, UserRole
from app.services.auth_service import create_admin

PASSWORD = "StrongPassword123"


def csrf(client):
    return client.cookies.get("dfi_csrf")


@pytest.mark.asyncio
async def test_register_login_and_password_is_only_stored_as_argon2(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    response = await client.post("/api/v1/auth/register", json={
        "full_name": "Test Investigator", "email": "Person@Example.com", "password": PASSWORD,
    })
    assert response.status_code == 201
    assert response.json()["email"] == "person@example.com"
    assert response.json()["role"] == "investigator"
    assert client.cookies.get("dfi_access")
    assert client.cookies.get("dfi_refresh")
    assert (await client.get("/api/v1/auth/me")).json()["id"] == response.json()["id"]
    async with app.state.test_sessions() as db:
        user = await db.scalar(select(User).where(User.email == "person@example.com"))
        assert user is not None
        assert user.password_hash != PASSWORD
        assert verify_password(PASSWORD, user.password_hash)
    duplicate = await client.post("/api/v1/auth/register", json={
        "full_name": "Duplicate", "email": "person@example.com", "password": PASSWORD,
    })
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_invalid_and_disabled_user_login_is_generic(client):
    bad = await client.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": PASSWORD})
    assert bad.status_code == 401
    assert bad.json() == {"detail": "Email or password is incorrect"}
    async with app.state.test_sessions() as db:
        db.add(User(full_name="Disabled", email="disabled@example.com", password_hash=hash_password(PASSWORD), is_active=False))
        await db.commit()
    disabled = await client.post("/api/v1/auth/login", json={"email": "disabled@example.com", "password": PASSWORD})
    assert disabled.status_code == 401


@pytest.mark.asyncio
async def test_seed_admin_updates_existing_account_password_and_revokes_sessions(client):
    async with app.state.test_sessions() as db:
        user = User(
            full_name="Existing Investigator",
            email="admin@example.com",
            password_hash=hash_password("OldPassword123"),
            role=UserRole.investigator,
        )
        db.add(user)
        await db.flush()
        db.add(RefreshSession(
            user_id=user.id,
            token_hash=hash_token("existing-refresh-token"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        await create_admin(db, "DocForensIQ Admin", "ADMIN@example.com", PASSWORD)
        await db.commit()
        await db.refresh(user)

        assert user.full_name == "DocForensIQ Admin"
        assert user.email == "admin@example.com"
        assert user.role == UserRole.admin
        assert user.is_active is True
        assert user.is_verified is True
        assert verify_password(PASSWORD, user.password_hash)
        assert not verify_password("OldPassword123", user.password_hash)
        session = await db.scalar(select(RefreshSession).where(RefreshSession.user_id == user.id))
        assert session is not None
        assert session.revoked_at is not None


@pytest.mark.asyncio
async def test_refresh_rotates_and_logout_revokes(client):
    async with app.state.test_sessions() as db:
        db.add(User(full_name="Analyst", email="analyst@example.com", password_hash=hash_password(PASSWORD), role=UserRole.investigator))
        await db.commit()
    response = await client.post("/api/v1/auth/login", json={"email": "analyst@example.com", "password": PASSWORD})
    assert response.status_code == 200
    old_refresh = client.cookies.get("dfi_refresh")
    rotated = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf(client)})
    assert rotated.status_code == 200
    new_refresh = client.cookies.get("dfi_refresh")
    assert new_refresh and new_refresh != old_refresh
    logout = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf(client)})
    assert logout.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    async with app.state.test_sessions() as db:
        sessions = (await db.scalars(select(RefreshSession))).all()
        assert len(sessions) == 2
        assert all(session.revoked_at is not None for session in sessions)


@pytest.mark.asyncio
async def test_dashboard_and_creator_only_investigation_access(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    first = await client.post("/api/v1/auth/register", json={"full_name": "First", "email": "first@example.com", "password": PASSWORD})
    assert first.status_code == 201
    await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf(client)})
    second = await client.post("/api/v1/auth/register", json={"full_name": "Second", "email": "second@example.com", "password": PASSWORD})
    assert second.status_code == 201
    no_csrf = await client.post("/api/v1/investigations", json={"title": "Missing CSRF token"})
    assert no_csrf.status_code == 403
    created = await client.post("/api/v1/investigations", headers={"X-CSRF-Token": csrf(client)}, json={"title": "Second user's case"})
    assert created.status_code == 201
    assert created.json()["case_number"].startswith("DF-")
    blank_title = await client.post(
        "/api/v1/investigations",
        headers={"X-CSRF-Token": csrf(client)},
        json={"title": "   "},
    )
    assert blank_title.status_code == 422
    investigation_id = created.json()["id"]
    detail = await client.get(f"/api/v1/investigations/{investigation_id}")
    assert detail.status_code == 200
    for invalid_update in ({"title": None}, {"title": "  "}, {"status": None}):
        response = await client.patch(
            f"/api/v1/investigations/{investigation_id}",
            headers={"X-CSRF-Token": csrf(client)},
            json=invalid_update,
        )
        assert response.status_code == 422
    update = await client.patch(
        f"/api/v1/investigations/{investigation_id}",
        headers={"X-CSRF-Token": csrf(client)},
        json={"title": "Updated case", "status": "in_review"},
    )
    assert update.status_code == 200
    filtered = await client.get("/api/v1/investigations?status=in_review&sort_by=title&order=asc")
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["title"] == "Updated case"
    assert (await client.get(f"/api/v1/investigations/{investigation_id}/documents")).json() == []
    summary = await client.get("/api/v1/dashboard/summary")
    assert summary.json() == {"total_investigations": 1, "active_investigations": 0, "in_review_investigations": 1, "completed_investigations": 0}
    assert len((await client.get("/api/v1/dashboard/recent-investigations")).json()) == 1
    assert len((await client.get("/api/v1/dashboard/activity")).json()) == 2
    await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf(client)})
    await client.post("/api/v1/auth/login", json={"email": "first@example.com", "password": PASSWORD})
    unauthorized = await client.get(f"/api/v1/investigations/{investigation_id}")
    assert unauthorized.status_code == 404
    async with app.state.test_sessions() as db:
        first_user = await db.scalar(select(User).where(User.email == "first@example.com"))
        assert first_user is not None
        first_user.role = UserRole.reviewer
        await db.commit()
    forbidden = await client.post("/api/v1/investigations", headers={"X-CSRF-Token": csrf(client)}, json={"title": "Not allowed"})
    assert forbidden.status_code == 403
    assert (await client.get("/api/v1/users")).status_code == 403


@pytest.mark.asyncio
async def test_password_reset_generic_response_single_use_and_session_revocation(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    registration = await client.post("/api/v1/auth/register", json={"full_name": "Reset User", "email": "reset@example.com", "password": PASSWORD})
    assert registration.status_code == 201
    await client.post("/api/v1/auth/password-reset/request", json={"email": "reset@example.com"})
    response = await client.post("/api/v1/auth/password-reset/request", json={"email": "absent@example.com"})
    assert response.status_code == 200
    assert response.json() == {"message": "If an active account matches that email, password reset instructions will be sent."}
    sink = json.loads((tmp_path / settings.email_sink_file).read_text(encoding="utf-8").splitlines()[0])
    token = parse_qs(urlparse(sink["reset_url"]).query)["token"][0]
    changed = await client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "password": "AnotherStrong123"})
    assert changed.status_code == 200
    async with app.state.test_sessions() as db:
        session = await db.scalar(select(RefreshSession))
        assert session is not None and session.revoked_at is not None
    reused = await client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "password": "DifferentStrong123"})
    assert reused.status_code == 400
    logged_in = await client.post("/api/v1/auth/login", json={"email": "reset@example.com", "password": "AnotherStrong123"})
    assert logged_in.status_code == 200
    weak_password = await client.post("/api/v1/auth/register", json={"full_name": "Weak", "email": "weak@example.com", "password": "short"})
    assert weak_password.status_code == 422


@pytest.mark.asyncio
async def test_empty_dashboard_returns_empty_aggregates(client, monkeypatch):
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    await client.post("/api/v1/auth/register", json={"full_name": "New User", "email": "new@example.com", "password": PASSWORD})
    summary = await client.get("/api/v1/dashboard/summary")
    assert summary.json() == {"total_investigations": 0, "active_investigations": 0, "in_review_investigations": 0, "completed_investigations": 0}
    assert (await client.get("/api/v1/dashboard/recent-investigations")).json() == []
    assert (await client.get("/api/v1/dashboard/activity")).json() == []


@pytest.mark.asyncio
async def test_expired_password_reset_token_is_rejected(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    await client.post("/api/v1/auth/register", json={"full_name": "Expired Token", "email": "expired@example.com", "password": PASSWORD})
    await client.post("/api/v1/auth/password-reset/request", json={"email": "expired@example.com"})
    sink = json.loads((tmp_path / settings.email_sink_file).read_text(encoding="utf-8").splitlines()[0])
    token = parse_qs(urlparse(sink["reset_url"]).query)["token"][0]
    async with app.state.test_sessions() as db:
        reset = await db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token)))
        assert reset is not None
        reset.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    result = await client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "password": "AnotherStrong123"})
    assert result.status_code == 400
