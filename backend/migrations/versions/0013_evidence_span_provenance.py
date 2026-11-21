"""Record extraction provenance on evidence spans.

Revision ID: 0013_evidence_span_provenance
Revises: 0012_pdf_forensic_artifacts
"""
from alembic import op
import sqlalchemy as sa


revision = "0013_evidence_span_provenance"
down_revision = "0012_pdf_forensic_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evidence_spans", sa.Column("extraction_method", sa.String(length=32), nullable=False, server_default="ocr"))
    op.add_column("evidence_spans", sa.Column("coordinate_space", sa.String(length=32), nullable=True))
    op.add_column("evidence_spans", sa.Column("source_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("evidence_spans", "source_sha256")
    op.drop_column("evidence_spans", "coordinate_space")
    op.drop_column("evidence_spans", "extraction_method")
