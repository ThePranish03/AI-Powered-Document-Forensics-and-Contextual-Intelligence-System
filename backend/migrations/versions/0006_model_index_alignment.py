"""Align investigation entity indexes with current ORM metadata.

Revision ID: 0006_model_index_alignment
Revises: 0005_correlation_review
"""
from alembic import op


revision = "0006_model_index_alignment"
down_revision = "0005_correlation_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_derived_graph_nodes_investigation_review", table_name="derived_graph_nodes")
    op.create_index("ix_audit_chain_events_investigation_id", "audit_chain_events", ["investigation_id"])
    op.create_index("ix_derived_graph_nodes_investigation_id", "derived_graph_nodes", ["investigation_id"])
    op.create_index("ix_findings_investigation_id", "findings", ["investigation_id"])


def downgrade() -> None:
    op.drop_index("ix_findings_investigation_id", table_name="findings")
    op.drop_index("ix_derived_graph_nodes_investigation_id", table_name="derived_graph_nodes")
    op.drop_index("ix_audit_chain_events_investigation_id", table_name="audit_chain_events")
    op.create_index(
        "ix_derived_graph_nodes_investigation_review",
        "derived_graph_nodes",
        ["investigation_id", "review_status"],
    )
