"""Persist investigator decisions for deterministic correlation candidates.

Revision ID: 0005_correlation_review
Revises: 0004_extraction_evidence
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_correlation_review"
down_revision = "0004_extraction_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("derived_graph_nodes") as batch_op:
        batch_op.add_column(sa.Column("review_status", sa.String(16), nullable=False, server_default="candidate"))
        batch_op.add_column(sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("review_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_derived_graph_nodes_reviewed_by_user_id_users",
            "users",
            ["reviewed_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_derived_graph_nodes_investigation_review",
        "derived_graph_nodes",
        ["investigation_id", "review_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_derived_graph_nodes_investigation_review", table_name="derived_graph_nodes")
    with op.batch_alter_table("derived_graph_nodes") as batch_op:
        batch_op.drop_constraint("fk_derived_graph_nodes_reviewed_by_user_id_users", type_="foreignkey")
        batch_op.drop_column("review_reason")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by_user_id")
        batch_op.drop_column("review_status")
