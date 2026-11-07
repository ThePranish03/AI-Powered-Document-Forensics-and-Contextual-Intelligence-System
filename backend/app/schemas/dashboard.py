from datetime import datetime
from pydantic import BaseModel
from app.models.entities import InvestigationStatus


class Summary(BaseModel):
    total_investigations: int
    active_investigations: int
    in_review_investigations: int
    completed_investigations: int


class RecentInvestigation(BaseModel):
    id: str
    case_number: str
    title: str
    status: InvestigationStatus
    document_count: int
    updated_at: datetime


class ActivityItem(BaseModel):
    id: str
    event_type: str
    summary: str
    created_at: datetime
    case_number: str | None


class DashboardResponse(BaseModel):
    summary: Summary
    recent_investigations: list[RecentInvestigation]
    recent_activity: list[ActivityItem]
