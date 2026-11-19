"""Persist investigator review disposition for visual candidate regions."""
from alembic import op
import sqlalchemy as sa


revision = "0010_visual_region_review"
down_revision = "0009_trufor_pages_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("visual_candidate_regions") as batch:
        batch.add_column(sa.Column("review_status", sa.String(length=24), nullable=False, server_default="needs_review"))
        batch.add_column(sa.Column("review_notes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_visual_candidate_regions_reviewed_by_user_id_users", "users", ["reviewed_by_user_id"], ["id"], ondelete="SET NULL")
        batch.alter_column("review_status", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("visual_candidate_regions") as batch:
        batch.drop_constraint("fk_visual_candidate_regions_reviewed_by_user_id_users", type_="foreignkey")
        batch.drop_column("reviewed_at")
        batch.drop_column("reviewed_by_user_id")
        batch.drop_column("review_notes")
        batch.drop_column("review_status")
