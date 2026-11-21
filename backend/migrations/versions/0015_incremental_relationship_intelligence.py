"""Add occurrence-level deterministic evidence and stable candidates."""
from alembic import op
import sqlalchemy as sa


revision = "0015_incremental_relationship_intelligence"
down_revision = "0014_processing_error_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deterministic_evidence_mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_type", sa.String(48), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.String(512), nullable=False),
        sa.Column("block_id", sa.String(100), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("extraction_method", sa.String(32), nullable=False),
        sa.Column("label_context", sa.String(120), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("coordinates", sa.JSON(), nullable=True),
        sa.Column("strength", sa.String(24), nullable=False),
        sa.Column("matcher_version", sa.String(48), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_det_mentions_investigation_key", "deterministic_evidence_mentions", ["investigation_id", "evidence_type", "normalized_value"])
    op.create_index("ix_det_mentions_document", "deterministic_evidence_mentions", ["document_id", "source_sha256"])
    op.create_index(
        "uq_det_mentions_source_occurrence", "deterministic_evidence_mentions",
        ["document_id", "source_sha256", "block_id", "evidence_type", "normalized_value", "start_offset", "end_offset", "matcher_version"], unique=True,
    )
    op.create_table(
        "relationship_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_key", sa.String(64), nullable=False),
        sa.Column("evidence_type", sa.String(48), nullable=False),
        sa.Column("normalized_value", sa.String(512), nullable=False),
        sa.Column("lifecycle_status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("review_history", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_relationship_candidates_key", "relationship_candidates", ["investigation_id", "candidate_key"], unique=True)
    op.create_index("ix_relationship_candidates_lifecycle", "relationship_candidates", ["investigation_id", "lifecycle_status"])


def downgrade() -> None:
    op.drop_table("relationship_candidates")
    op.drop_table("deterministic_evidence_mentions")
