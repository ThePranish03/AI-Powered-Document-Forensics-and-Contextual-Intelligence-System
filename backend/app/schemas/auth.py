from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import password_is_valid
from app.models.entities import UserRole


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name cannot be empty")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not password_is_valid(value):
            raise ValueError("Password must be at least 12 characters and include uppercase, lowercase, and a number")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not password_is_valid(value):
            raise ValueError("Password must be at least 12 characters and include uppercase, lowercase, and a number")
        return value


class UserResponse(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    role: UserRole
    permissions: list[str]
