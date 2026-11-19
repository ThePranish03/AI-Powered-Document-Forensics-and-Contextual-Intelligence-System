"""Persist visual-forensics engine pages, artifacts and candidate regions."""
from alembic import op
import sqlalchemy as sa


revision = "0009_trufor_pages_artifacts"
down_revision = "0008_visual_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visual_analysis_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("page_id", sa.Uuid(), nullable=True),
        sa.Column("artifact_type", sa.String(length=48), nullable=False),
        sa.Column("storage_key", sa.String(length=96), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=True),
        sa.Column("height_px", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("derived_from_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("transform_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["derived_from_artifact_id"], ["visual_analysis_artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        
        sa.ForeignKeyConstraint(["run_id"], ["visual_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_table(
        "visual_analysis_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("engine", sa.String(length=48), nullable=True),
        sa.Column("engine_version", sa.String(length=64), nullable=True),
        sa.Column("checkpoint_sha256", sa.String(length=64), nullable=True),
        sa.Column("device", sa.String(length=32), nullable=True),
        sa.Column("source_page_width_points", sa.Float(), nullable=True),
        sa.Column("source_page_height_points", sa.Float(), nullable=True),
        sa.Column("render_width_px", sa.Integer(), nullable=True),
        sa.Column("render_height_px", sa.Integer(), nullable=True),
        sa.Column("render_dpi", sa.Integer(), nullable=True),
        sa.Column("page_rotation", sa.Integer(), nullable=True),
        sa.Column("transform_json", sa.JSON(), nullable=False),
        sa.Column("integrity_score", sa.Float(), nullable=True),
        sa.Column("localization_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("reliability_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("noiseprint_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("diagnostics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["localization_artifact_id"], ["visual_analysis_artifacts.id"], name="fk_visual_analysis_pages_localization_artifact", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reliability_artifact_id"], ["visual_analysis_artifacts.id"], name="fk_visual_analysis_pages_reliability_artifact", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["noiseprint_artifact_id"], ["visual_analysis_artifacts.id"], name="fk_visual_analysis_pages_noiseprint_artifact", ondelete="SET NULL"),

        
        
        
        sa.ForeignKeyConstraint(["run_id"], ["visual_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "page_number"),
    )
    with op.batch_alter_table("visual_analysis_artifacts") as batch_op:
        batch_op.create_foreign_key("fk_visual_analysis_artifacts_page_id_visual_analysis_pages", "visual_analysis_pages", ["page_id"], ["id"], ondelete="CASCADE")
    op.create_table(
        "visual_candidate_regions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("region_index", sa.Integer(), nullable=False),
        sa.Column("bbox_pixel", sa.JSON(), nullable=False),
        sa.Column("bbox_pdf_points", sa.JSON(), nullable=False),
        sa.Column("area_px", sa.Integer(), nullable=False),
        sa.Column("mean_localization_score", sa.Float(), nullable=True),
        sa.Column("max_localization_score", sa.Float(), nullable=True),
        sa.Column("mean_reliability_value", sa.Float(), nullable=True),
        sa.Column("threshold_config", sa.JSON(), nullable=False),
        sa.Column("postprocess_version", sa.String(length=48), nullable=False),
        sa.Column("linked_ocr_block_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("page_id", "region_index"),
    )
    op.create_index("ix_visual_analysis_artifacts_document_id", "visual_analysis_artifacts", ["document_id"])
    op.create_index("ix_visual_analysis_artifacts_investigation_id", "visual_analysis_artifacts", ["investigation_id"])
    op.create_index("ix_visual_analysis_artifacts_page_id", "visual_analysis_artifacts", ["page_id"])
    op.create_index("ix_visual_analysis_artifacts_run_id", "visual_analysis_artifacts", ["run_id"])
    op.create_index("ix_visual_analysis_pages_run_id", "visual_analysis_pages", ["run_id"])
    op.create_index("ix_visual_artifacts_run_type", "visual_analysis_artifacts", ["run_id", "artifact_type"])
    op.create_index("ix_visual_artifacts_investigation_created", "visual_analysis_artifacts", ["investigation_id", "created_at"])
    op.create_index("ix_visual_candidate_regions_page_id", "visual_candidate_regions", ["page_id"])


def downgrade() -> None:
    op.drop_index("ix_visual_candidate_regions_page_id", table_name="visual_candidate_regions")
    op.drop_index("ix_visual_artifacts_investigation_created", table_name="visual_analysis_artifacts")
    op.drop_index("ix_visual_artifacts_run_type", table_name="visual_analysis_artifacts")
    op.drop_index("ix_visual_analysis_pages_run_id", table_name="visual_analysis_pages")
    op.drop_index("ix_visual_analysis_artifacts_run_id", table_name="visual_analysis_artifacts")
    op.drop_index("ix_visual_analysis_artifacts_page_id", table_name="visual_analysis_artifacts")
    op.drop_index("ix_visual_analysis_artifacts_investigation_id", table_name="visual_analysis_artifacts")
    op.drop_index("ix_visual_analysis_artifacts_document_id", table_name="visual_analysis_artifacts")
    op.drop_table("visual_candidate_regions")
    with op.batch_alter_table("visual_analysis_pages") as batch_op:
        batch_op.drop_constraint("fk_visual_analysis_pages_localization_artifact", type_="foreignkey")
        batch_op.drop_constraint("fk_visual_analysis_pages_reliability_artifact", type_="foreignkey")
        batch_op.drop_constraint("fk_visual_analysis_pages_noiseprint_artifact", type_="foreignkey")
    with op.batch_alter_table("visual_analysis_artifacts") as batch_op:
        batch_op.drop_constraint("fk_visual_analysis_artifacts_page_id_visual_analysis_pages", type_="foreignkey")
    op.drop_table("visual_analysis_artifacts")
    op.drop_table("visual_analysis_pages")