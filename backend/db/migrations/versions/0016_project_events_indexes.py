"""add project_events indexes and autovacuum tuning

Revision ID: 0016_project_events_indexes
Revises: 0015_project_generation_mode
Create Date: 2026-09-10 00:00:00.000000

project_events is written once per streamed writer chunk and was missing
indexes on (project_id, ...) so event polling and chunk cleanup degraded
into sequential scans as the table grew. The high-churn streaming writes
also outpace default autovacuum settings, causing table bloat under
concurrent workers.
"""

from alembic import op


revision = "0016_project_events_indexes"
down_revision = "0015_project_generation_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_project_events_project_id_id",
        "project_events",
        ["project_id", "id"],
    )
    op.create_index(
        "ix_project_events_cleanup",
        "project_events",
        ["project_id", "chapter_number", "event_type"],
    )
    op.execute(
        "ALTER TABLE project_events SET ("
        "autovacuum_vacuum_scale_factor = 0.01, "
        "autovacuum_vacuum_threshold = 100, "
        "autovacuum_analyze_scale_factor = 0.01)"
    )
    op.execute(
        "ALTER TABLE chapter_attempts SET ("
        "autovacuum_vacuum_scale_factor = 0.05, "
        "autovacuum_vacuum_threshold = 50)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE project_events RESET (autovacuum_vacuum_scale_factor, autovacuum_vacuum_threshold, autovacuum_analyze_scale_factor)")
    op.execute("ALTER TABLE chapter_attempts RESET (autovacuum_vacuum_scale_factor, autovacuum_vacuum_threshold)")
    op.drop_index("ix_project_events_cleanup", table_name="project_events")
    op.drop_index("ix_project_events_project_id_id", table_name="project_events")
