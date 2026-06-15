"""add system settings and writer streaming flag

Revision ID: 0005_sys_settings_ws
Revises: 0004_normalize_model_extra_cfg
Create Date: 2026-03-30 00:00:05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_sys_settings_ws"
down_revision = "0004_normalize_model_extra_cfg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("writer_streaming_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("default_total_chapters", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("default_auto_mode", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("default_max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "system_model_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("temperature", sa.Numeric(3, 2), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("extra_config", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("role", name="uq_system_model_role"),
    )


def downgrade() -> None:
    op.drop_table("system_model_configs")
    op.drop_table("system_settings")
    op.drop_column("projects", "writer_streaming_enabled")
