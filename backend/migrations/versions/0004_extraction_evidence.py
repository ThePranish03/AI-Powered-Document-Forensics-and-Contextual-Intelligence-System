"""Persist OCR extraction history and canonical page/span evidence.

Revision ID: 0004_extraction_evidence
Revises: 0003_evidence_analysis
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_extraction_evidence"
down_revision = "0003_evidence_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("source_description", sa.Text(), nullable=True))
    op.create_index("ix_documents_uploaded_by_user_id", "documents", ["uploaded_by_user_id"])
    with op.batch_alter_table("documents") as batch_op:
        batch_op.create_foreign_key(
            "fk_documents_uploaded_by_user_id_users",
            "users",
            ["uploaded_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(64), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("raw_result", sa.JSON(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extraction_runs_document_id", "extraction_runs", ["document_id"])
    op.create_index("ix_extraction_runs_document_created", "extraction_runs", ["document_id", "created_at"])
    op.create_index("ix_extraction_runs_document_run", "extraction_runs", ["document_id", "run_number"], unique=True)
    op.create_table(
        "evidence_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_run_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("preview_key", sa.String(160), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_pages_document_id", "evidence_pages", ["document_id"])
    op.create_index("ix_evidence_pages_extraction_run_id", "evidence_pages", ["extraction_run_id"])
    op.create_index("ix_evidence_pages_run_page", "evidence_pages", ["extraction_run_id", "page_number"], unique=True)
    op.create_table(
        "evidence_spans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_run_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=True),
        sa.Column("block_id", sa.String(100), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("bounding_box", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_reference", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["evidence_pages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_spans_document_id", "evidence_spans", ["document_id"])
    op.create_index("ix_evidence_spans_extraction_run_id", "evidence_spans", ["extraction_run_id"])
    op.create_index("ix_evidence_spans_page_id", "evidence_spans", ["page_id"])
    op.create_index("ix_evidence_spans_run_block", "evidence_spans", ["extraction_run_id", "block_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_evidence_spans_run_block", table_name="evidence_spans")
    op.drop_index("ix_evidence_spans_page_id", table_name="evidence_spans")
    op.drop_index("ix_evidence_spans_extraction_run_id", table_name="evidence_spans")
    op.drop_index("ix_evidence_spans_document_id", table_name="evidence_spans")
    op.drop_table("evidence_spans")
    op.drop_index("ix_evidence_pages_run_page", table_name="evidence_pages")
    op.drop_index("ix_evidence_pages_extraction_run_id", table_name="evidence_pages")
    op.drop_index("ix_evidence_pages_document_id", table_name="evidence_pages")
    op.drop_table("evidence_pages")
    op.drop_index("ix_extraction_runs_document_run", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_document_created", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_document_id", table_name="extraction_runs")
    op.drop_table("extraction_runs")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("fk_documents_uploaded_by_user_id_users", type_="foreignkey")
    op.drop_index("ix_documents_uploaded_by_user_id", table_name="documents")
    op.drop_column("documents", "source_description")
    op.drop_column("documents", "uploaded_by_user_id")
