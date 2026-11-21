"""Persist safe processing failure codes and retryability."""
from alembic import op
import sqlalchemy as sa


revision = "0014_processing_error_diagnostics"
down_revision = "0013_evidence_span_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("processing_error_code", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("processing_error_retryable", sa.Boolean(), nullable=True))
    op.add_column("documents", sa.Column("relationship_extraction_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("documents", "relationship_extraction_attempts")
    op.drop_column("documents", "processing_error_retryable")
    op.drop_column("documents", "processing_error_code")
