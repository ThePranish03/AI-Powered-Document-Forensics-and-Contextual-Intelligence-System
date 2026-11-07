"""Add secure document storage metadata and OCR result persistence.

Revision ID: 0002_documents_ocr
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_documents_ocr"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("storage_key", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("sha256", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("processing_error", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("processing_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_documents_storage_key", "documents", ["storage_key"], unique=True)
    op.create_table(
        "ocr_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(64), nullable=True),
        sa.Column("raw_result", sa.JSON(), nullable=False),
        sa.Column("blocks", sa.JSON(), nullable=False),
        sa.Column("recognized_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocr_results_document_id", "ocr_results", ["document_id"], unique=True)
    op.create_table(
        "relationship_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_relationship_analyses_document_id", "relationship_analyses", ["document_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_relationship_analyses_document_id", table_name="relationship_analyses")
    op.drop_table("relationship_analyses")
    op.drop_index("ix_ocr_results_document_id", table_name="ocr_results")
    op.drop_table("ocr_results")
    op.drop_index("ix_documents_storage_key", table_name="documents")
    op.drop_column("documents", "processing_completed_at")
    op.drop_column("documents", "processing_started_at")
    op.drop_column("documents", "processing_error")
    op.drop_column("documents", "sha256")
    op.drop_column("documents", "storage_key")
    op.drop_column("documents", "processing_attempts")
