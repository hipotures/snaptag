"""initial schema

Revision ID: 20260401_01
Revises:
Create Date: 2026-04-01 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "20260401_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("sha256", name="uq_blobs_sha256"),
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("blob_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("source_file_mtime", sa.DateTime(), nullable=True),
        sa.Column("embedded_metadata_json", sa.Text(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["blob_id"], ["blobs.id"]),
    )
    op.create_index("ix_assets_blob_id", "assets", ["blob_id"], unique=False)

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
    )
    op.create_index("ix_pipeline_runs_asset_id", "pipeline_runs", ["asset_id"], unique=False)

    op.create_table(
        "stage_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pipeline_run_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("stage_name", sa.String(length=32), nullable=False),
        sa.Column("stage_version", sa.String(length=64), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("artifact_ref", sa.String(length=512), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.UniqueConstraint(
            "pipeline_run_id",
            "stage_name",
            name="uq_stage_results_pipeline_run_stage_name",
        ),
    )
    op.create_index(
        "ix_stage_results_asset_id", "stage_results", ["asset_id"], unique=False
    )
    op.create_index(
        "ix_stage_results_pipeline_run_id",
        "stage_results",
        ["pipeline_run_id"],
        unique=False,
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_index("ix_stage_results_pipeline_run_id", table_name="stage_results")
    op.drop_index("ix_stage_results_asset_id", table_name="stage_results")
    op.drop_table("stage_results")
    op.drop_index("ix_pipeline_runs_asset_id", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index("ix_assets_blob_id", table_name="assets")
    op.drop_table("assets")
    op.drop_table("blobs")
