"""Persist visual-forensics runs and observations."""
from alembic import op
import sqlalchemy as sa


revision = "0008_visual_analysis"
down_revision = "0007_dossier_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visual_analysis_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("analyzer_version", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_visual_analysis_runs_document_id", "visual_analysis_runs", ["document_id"])
    op.create_index("ix_visual_analysis_runs_investigation_id", "visual_analysis_runs", ["investigation_id"])
    op.create_index("ix_visual_runs_document_created", "visual_analysis_runs", ["document_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_visual_runs_document_created", table_name="visual_analysis_runs")
    op.drop_index("ix_visual_analysis_runs_investigation_id", table_name="visual_analysis_runs")
    op.drop_index("ix_visual_analysis_runs_document_id", table_name="visual_analysis_runs")
    op.drop_table("visual_analysis_runs")
