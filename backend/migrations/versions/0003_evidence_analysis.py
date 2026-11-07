"""Persist findings, timeline, audit chain, dossiers, and OCR evaluations.

Revision ID: 0003_evidence_analysis
Revises: 0002_documents_ocr
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_evidence_analysis"
down_revision = "0002_documents_ocr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_chain_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_chain_investigation_created", "audit_chain_events", ["investigation_id", "created_at"])
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("finding_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("analyzer_name", sa.String(64), nullable=False),
        sa.Column("analyzer_version", sa.String(32), nullable=False),
        sa.Column("method", sa.String(80), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.Text(), nullable=False),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_findings_investigation_status", "findings", ["investigation_id", "status"])
    op.create_index("ix_findings_fingerprint", "findings", ["fingerprint"])
    op.create_table(
        "timeline_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("raw_date", sa.String(80), nullable=False),
        sa.Column("interpreted_date", sa.String(32), nullable=True),
        sa.Column("ambiguous", sa.Boolean(), nullable=False),
        sa.Column("timezone_known", sa.Boolean(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("annotation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_timeline_events_investigation_id", "timeline_events", ["investigation_id"])
    op.create_table(
        "derived_graph_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_id", sa.String(160), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.String(32), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_derived_graph_investigation_canonical", "derived_graph_nodes", ["investigation_id", "canonical_id"], unique=True)
    op.create_table(
        "dossier_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dossier_snapshots_investigation_id", "dossier_snapshots", ["investigation_id"])
    op.create_table(
        "ocr_evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_name", sa.String(160), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("samples", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocr_evaluation_runs_investigation_id", "ocr_evaluation_runs", ["investigation_id"])


def downgrade() -> None:
    op.drop_index("ix_ocr_evaluation_runs_investigation_id", table_name="ocr_evaluation_runs")
    op.drop_table("ocr_evaluation_runs")
    op.drop_index("ix_dossier_snapshots_investigation_id", table_name="dossier_snapshots")
    op.drop_table("dossier_snapshots")
    op.drop_index("ix_derived_graph_investigation_canonical", table_name="derived_graph_nodes")
    op.drop_table("derived_graph_nodes")
    op.drop_index("ix_timeline_events_investigation_id", table_name="timeline_events")
    op.drop_table("timeline_events")
    op.drop_index("ix_findings_fingerprint", table_name="findings")
    op.drop_index("ix_findings_investigation_status", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_audit_chain_investigation_created", table_name="audit_chain_events")
    op.drop_table("audit_chain_events")
