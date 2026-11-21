"""Persist private image artifacts extracted from PDF objects."""
from alembic import op
import sqlalchemy as sa


revision = "0012_pdf_forensic_artifacts"
down_revision = "0011_pdf_forensic_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pdf_forensic_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("object_number", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["pdf_forensic_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pdf_forensic_artifacts_run_object", "pdf_forensic_artifacts", ["run_id", "object_number"])
    op.create_index("ix_pdf_forensic_artifacts_document_id", "pdf_forensic_artifacts", ["document_id"])
    op.create_index("ix_pdf_forensic_artifacts_investigation_id", "pdf_forensic_artifacts", ["investigation_id"])
    op.create_index("ix_pdf_forensic_artifacts_run_id", "pdf_forensic_artifacts", ["run_id"])
    op.create_index("ix_pdf_forensic_artifacts_storage_key", "pdf_forensic_artifacts", ["storage_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_pdf_forensic_artifacts_storage_key", table_name="pdf_forensic_artifacts")
    op.drop_index("ix_pdf_forensic_artifacts_run_id", table_name="pdf_forensic_artifacts")
    op.drop_index("ix_pdf_forensic_artifacts_investigation_id", table_name="pdf_forensic_artifacts")
    op.drop_index("ix_pdf_forensic_artifacts_document_id", table_name="pdf_forensic_artifacts")
    op.drop_index("ix_pdf_forensic_artifacts_run_object", table_name="pdf_forensic_artifacts")
    op.drop_table("pdf_forensic_artifacts")
