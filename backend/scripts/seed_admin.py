import asyncio
import os

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.db.session import SessionLocal
from app.core.security import password_is_valid
from app.services.auth_service import create_admin


async def main() -> None:
    email = os.getenv("ADMIN_EMAIL", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "")
    name = os.getenv("ADMIN_NAME", "DocForensIQ Admin")
    if not email or not password_is_valid(password):
        raise SystemExit("Set ADMIN_EMAIL and ADMIN_PASSWORD (12+ characters with uppercase, lowercase, and a number) before seeding.")
    try:
        email = str(TypeAdapter(EmailStr).validate_python(email))
    except ValidationError:
        raise SystemExit("ADMIN_EMAIL must be a valid email address.") from None
    async with SessionLocal() as db:
        await create_admin(db, name, email, password)
        await db.commit()
    print(f"Admin account ensured for {email}")


if __name__ == "__main__":
    asyncio.run(main())
