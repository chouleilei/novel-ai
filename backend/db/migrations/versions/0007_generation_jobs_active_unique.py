"""add active generation job unique index

Revision ID: 0007_gen_jobs_active_uq
Revises: 0006_system_runtime_settings
Create Date: 2026-04-03 00:00:07
"""

from alembic import op


revision = "0007_gen_jobs_active_uq"
down_revision = "0006_system_runtime_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_generation_jobs_active_project_chapter_type",
        "generation_jobs",
        ["project_id", "chapter_number", "job_type"],
        unique=True,
        postgresql_where="chapter_number IS NOT NULL AND status IN ('queued', 'leased')",
    )


def downgrade() -> None:
    op.drop_index("uq_generation_jobs_active_project_chapter_type", table_name="generation_jobs")
