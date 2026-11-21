import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    admin = "admin"
    investigator = "investigator"
    reviewer = "reviewer"


class InvestigationStatus(str, enum.Enum):
    draft = "draft"
    in_progress = "in_progress"
    in_review = "in_review"
    completed = "completed"
    archived = "archived"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), default=UserRole.investigator)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_sessions: Mapped[list["RefreshSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    user: Mapped[User] = relationship(back_populates="refresh_sessions")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (Index("ix_investigations_owner_updated", "created_by_user_id", "updated_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[InvestigationStatus] = mapped_column(Enum(InvestigationStatus, name="investigation_status"), default=InvestigationStatus.draft)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    documents: Mapped[list["Document"]] = relationship(back_populates="investigation", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    source_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    processing_status: Mapped[str] = mapped_column(String(32), default="pending")
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True, default=lambda: uuid.uuid4().hex)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relationship_extraction_attempts: Mapped[int] = mapped_column(Integer, default=0)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    investigation: Mapped[Investigation] = relationship(back_populates="documents")


class OCRResult(Base):
    __tablename__ = "ocr_results"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_result: Mapped[dict] = mapped_column(JSON)
    blocks: Mapped[list] = mapped_column(JSON, default=list)
    recognized_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        Index("ix_extraction_runs_document_created", "document_id", "created_at"),
        Index("ix_extraction_runs_document_run", "document_id", "run_number", unique=True),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    run_number: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    raw_result: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidencePage(Base):
    __tablename__ = "evidence_pages"
    __table_args__ = (Index("ix_evidence_pages_run_page", "extraction_run_id", "page_number", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_runs.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    preview_key: Mapped[str | None] = mapped_column(String(160), nullable=True)


class EvidenceSpan(Base):
    __tablename__ = "evidence_spans"
    __table_args__ = (Index("ix_evidence_spans_run_block", "extraction_run_id", "block_id", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extraction_runs.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence_pages.id", ondelete="SET NULL"), nullable=True, index=True)
    block_id: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    bounding_box: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(32), default="ocr")
    coordinate_space: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RelationshipAnalysis(Base):
    __tablename__ = "relationship_analyses"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), unique=True, index=True)
    schema_version: Mapped[str] = mapped_column(String(64), default="relationship_extraction_v1")
    model: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActivityEvent(Base):
    __tablename__ = "activity_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class AuditChainEvent(Base):
    __tablename__ = "audit_chain_events"
    __table_args__ = (Index("ix_audit_chain_investigation_created", "investigation_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_investigation_status", "investigation_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    finding_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), default="candidate")
    analyzer_name: Mapped[str] = mapped_column(String(64))
    analyzer_version: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    limitations: Mapped[str] = mapped_column(Text)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VisualAnalysisRun(Base):
    __tablename__ = "visual_analysis_runs"
    __table_args__ = (Index("ix_visual_runs_document_created", "document_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    analyzer_version: Mapped[str] = mapped_column(String(32), default="1")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VisualAnalysisArtifact(Base):
    __tablename__ = "visual_analysis_artifacts"
    __table_args__ = (
        Index("ix_visual_artifacts_run_type", "run_id", "artifact_type"),
        Index("ix_visual_artifacts_investigation_created", "investigation_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visual_analysis_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visual_analysis_pages.id", ondelete="CASCADE"), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(48))
    storage_key: Mapped[str] = mapped_column(String(96), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    mime_type: Mapped[str] = mapped_column(String(64))
    width_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    derived_from_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visual_analysis_artifacts.id", ondelete="SET NULL"), nullable=True)
    transform_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VisualAnalysisPage(Base):
    __tablename__ = "visual_analysis_pages"
    __table_args__ = (Index("uq_visual_analysis_pages_run_page", "run_id", "page_number", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visual_analysis_runs.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    engine: Mapped[str | None] = mapped_column(String(48), nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_page_width_points: Mapped[float | None] = mapped_column(nullable=True)
    source_page_height_points: Mapped[float | None] = mapped_column(nullable=True)
    render_width_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    render_height_px: Mapped[int | None] = mapped_column(Integer, nullable=True)
    render_dpi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_rotation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transform_json: Mapped[dict] = mapped_column(JSON, default=dict)
    integrity_score: Mapped[float | None] = mapped_column(nullable=True)
    localization_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visual_analysis_artifacts.id", ondelete="SET NULL"), nullable=True)
    reliability_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visual_analysis_artifacts.id", ondelete="SET NULL"), nullable=True)
    noiseprint_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visual_analysis_artifacts.id", ondelete="SET NULL"), nullable=True)
    diagnostics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VisualCandidateRegion(Base):
    __tablename__ = "visual_candidate_regions"
    __table_args__ = (Index("uq_visual_candidate_regions_page_index", "page_id", "region_index", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visual_analysis_pages.id", ondelete="CASCADE"), index=True)
    region_index: Mapped[int] = mapped_column(Integer)
    bbox_pixel: Mapped[dict] = mapped_column(JSON)
    bbox_pdf_points: Mapped[dict] = mapped_column(JSON)
    area_px: Mapped[int] = mapped_column(Integer)
    mean_localization_score: Mapped[float | None] = mapped_column(nullable=True)
    max_localization_score: Mapped[float | None] = mapped_column(nullable=True)
    mean_reliability_value: Mapped[float | None] = mapped_column(nullable=True)
    threshold_config: Mapped[dict] = mapped_column(JSON, default=dict)
    postprocess_version: Mapped[str] = mapped_column(String(48))
    linked_ocr_block_ids: Mapped[list] = mapped_column(JSON, default=list)
    review_status: Mapped[str] = mapped_column(String(24), default="needs_review")
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PdfForensicRun(Base):
    """Immutable structural-analysis snapshot bound to the original evidence hash."""
    __tablename__ = "pdf_forensic_runs"
    __table_args__ = (Index("ix_pdf_forensic_runs_document_created", "document_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    parser_name: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(JSON)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PdfForensicArtifact(Base):
    """Private, independently hashed object extracted from a PDF source."""
    __tablename__ = "pdf_forensic_artifacts"
    __table_args__ = (Index("ix_pdf_forensic_artifacts_run_object", "run_id", "object_number"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pdf_forensic_runs.id", ondelete="CASCADE"), index=True)
    object_number: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mime_type: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    width_px: Mapped[int] = mapped_column(Integer)
    height_px: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TimelineEvent(Base):
    __tablename__ = "timeline_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    raw_date: Mapped[str] = mapped_column(String(80))
    interpreted_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone_known: Mapped[bool] = mapped_column(Boolean, default=False)
    event_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column()
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    annotation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DerivedGraphNode(Base):
    __tablename__ = "derived_graph_nodes"
    __table_args__ = (Index("ix_derived_graph_investigation_canonical", "investigation_id", "canonical_id", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    canonical_id: Mapped[str] = mapped_column(String(160))
    label: Mapped[str] = mapped_column(String(300))
    entity_type: Mapped[str] = mapped_column(String(32))
    document_ids: Mapped[list] = mapped_column(JSON, default=list)
    resolution: Mapped[str] = mapped_column(String(32))
    basis: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(16), default="candidate", server_default="candidate")
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeterministicEvidenceMention(Base):
    """One source occurrence of a typed deterministic identifier."""
    __tablename__ = "deterministic_evidence_mentions"
    __table_args__ = (
        Index("ix_det_mentions_investigation_key", "investigation_id", "evidence_type", "normalized_value"),
        Index("ix_det_mentions_document", "document_id", "source_sha256"),
        Index("uq_det_mentions_source_occurrence", "document_id", "source_sha256", "block_id", "evidence_type", "normalized_value", "start_offset", "end_offset", "matcher_version", unique=True),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    evidence_type: Mapped[str] = mapped_column(String(48))
    original_text: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[str] = mapped_column(String(512))
    block_id: Mapped[str] = mapped_column(String(100))
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(32))
    label_context: Mapped[str] = mapped_column(String(120))
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    coordinates: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    strength: Mapped[str] = mapped_column(String(24))
    matcher_version: Mapped[str] = mapped_column(String(48))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RelationshipCandidate(Base):
    """Stable investigation-scoped candidate with independent review state."""
    __tablename__ = "relationship_candidates"
    __table_args__ = (
        Index("uq_relationship_candidates_key", "investigation_id", "candidate_key", unique=True),
        Index("ix_relationship_candidates_lifecycle", "investigation_id", "lifecycle_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"))
    candidate_key: Mapped[str] = mapped_column(String(64))
    evidence_type: Mapped[str] = mapped_column(String(48))
    normalized_value: Mapped[str] = mapped_column(String(512))
    lifecycle_status: Mapped[str] = mapped_column(String(24), default="active", server_default="active")
    review_status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    document_ids: Mapped[list] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DossierSnapshot(Base):
    __tablename__ = "dossier_snapshots"
    __table_args__ = (Index("uq_dossier_snapshot_investigation_version", "investigation_id", "version", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    html: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OcrEvaluationRun(Base):
    __tablename__ = "ocr_evaluation_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    dataset_name: Mapped[str] = mapped_column(String(160))
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON)
    samples: Mapped[list] = mapped_column(JSON, default=list)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
