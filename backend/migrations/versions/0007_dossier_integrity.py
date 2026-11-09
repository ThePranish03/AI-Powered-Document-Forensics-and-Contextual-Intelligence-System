"""Add integrity digests and per-investigation dossier version uniqueness.

Revision ID: 0007_dossier_integrity
Revises: 0006_model_index_alignment
"""
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "0007_dossier_integrity"
down_revision = "0006_model_index_alignment"
branch_labels = None
depends_on = None


def _digest(snapshot, report_html: str) -> str:
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    snapshot = dict(snapshot or {})
    report = dict(snapshot.get("report") or {})
    report.pop("integrity_sha256", None)
    snapshot["report"] = report
    content = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((content + "\n" + report_html).encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("dossier_snapshots", sa.Column("content_sha256", sa.String(length=64), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, snapshot, html FROM dossier_snapshots")).mappings()
    for row in rows:
        connection.execute(
            sa.text("UPDATE dossier_snapshots SET content_sha256 = :digest WHERE id = :id"),
            {"digest": _digest(row["snapshot"], row["html"]), "id": row["id"]},
        )
    with op.batch_alter_table("dossier_snapshots") as batch_op:
        batch_op.alter_column("content_sha256", nullable=False)
    op.create_index(
        "uq_dossier_snapshot_investigation_version",
        "dossier_snapshots",
        ["investigation_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_dossier_snapshot_investigation_version", table_name="dossier_snapshots")
    op.drop_column("dossier_snapshots", "content_sha256")
