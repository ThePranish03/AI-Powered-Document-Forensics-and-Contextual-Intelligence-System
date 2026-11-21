"""Persist source-bound PDF structural analysis snapshots."""
from alembic import op
import sqlalchemy as sa


revision = "0011_pdf_forensic_runs"
down_revision = "0010_visual_region_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pdf_forensic_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("parser_name", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pdf_forensic_runs_document_created", "pdf_forensic_runs", ["document_id", "created_at"])
    op.create_index("ix_pdf_forensic_runs_document_id", "pdf_forensic_runs", ["document_id"])
    op.create_index("ix_pdf_forensic_runs_investigation_id", "pdf_forensic_runs", ["investigation_id"])


def downgrade() -> None:
    op.drop_index("ix_pdf_forensic_runs_investigation_id", table_name="pdf_forensic_runs")
    op.drop_index("ix_pdf_forensic_runs_document_id", table_name="pdf_forensic_runs")
    op.drop_index("ix_pdf_forensic_runs_document_created", table_name="pdf_forensic_runs")
    op.drop_table("pdf_forensic_runs")
