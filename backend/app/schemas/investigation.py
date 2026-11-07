from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from app.models.entities import InvestigationStatus


class InvestigationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title cannot be empty")
        return normalized


class InvestigationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: InvestigationStatus | None = None


class InvestigationResponse(BaseModel):
    id: str
    case_number: str
    title: str
    description: str | None
    status: InvestigationStatus
    document_count: int
    created_at: datetime
    updated_at: datetime


class DocumentResponse(BaseModel):
    id: str
    filename: str
    source_description: str | None = None
    uploaded_by_user_id: str | None = None
    content_type: str
    size_bytes: int
    processing_status: str
    attempts_remaining: int = 3
    created_at: datetime
    sha256: str | None = None
