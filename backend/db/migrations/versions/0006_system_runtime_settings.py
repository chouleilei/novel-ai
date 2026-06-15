"""add system runtime settings

Revision ID: 0006_system_runtime_settings
Revises: 0005_sys_settings_ws
Create Date: 2026-03-30 00:00:06
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_system_runtime_settings"
down_revision = "0005_sys_settings_ws"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_runtime_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("review_overall_score_threshold", sa.Numeric(4, 2), nullable=False, server_default="8.0"),
        sa.Column("review_outline_score_threshold", sa.Numeric(4, 2), nullable=False, server_default="8.0"),
        sa.Column("review_instruction_score_threshold", sa.Numeric(4, 2), nullable=False, server_default="8.0"),
        sa.Column("memory_auto_apply_confidence_threshold", sa.Numeric(4, 2), nullable=False, server_default="0.75"),
        sa.Column("writer_target_input_tokens", sa.Integer(), nullable=False, server_default="64000"),
        sa.Column("writer_hard_limit_tokens", sa.Integer(), nullable=False, server_default="96000"),
        sa.Column("critic_target_input_tokens", sa.Integer(), nullable=False, server_default="32000"),
        sa.Column("critic_hard_limit_tokens", sa.Integer(), nullable=False, server_default="48000"),
        sa.Column("llm_stage_timeout_seconds", sa.Numeric(8, 2), nullable=False, server_default="180.0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_runtime_settings")
