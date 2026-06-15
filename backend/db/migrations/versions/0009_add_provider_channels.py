"""add provider channels table and channel_id fk to model configs

Revision ID: 0009_add_provider_channels
Revises: 0008_project_auto_accept_review
Create Date: 2026-04-09 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_add_provider_channels"
down_revision = "0008_project_auto_accept_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建 provider_channels 表
    op.create_table(
        "provider_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("default_model_name", sa.String(length=200), nullable=False),
        sa.Column("api_key_env_var", sa.String(length=100), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # 2. 为 project_model_configs 表添加 channel_id 外键列
    op.add_column(
        "project_model_configs",
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_model_configs_channel_id",
        "project_model_configs",
        "provider_channels",
        ["channel_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. 为 system_model_configs 表添加 channel_id 外键列
    op.add_column(
        "system_model_configs",
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_system_model_configs_channel_id",
        "system_model_configs",
        "provider_channels",
        ["channel_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # 1. 删除 system_model_configs 表的外键和列
    op.drop_constraint("fk_system_model_configs_channel_id", "system_model_configs", type_="foreignkey")
    op.drop_column("system_model_configs", "channel_id")

    # 2. 删除 project_model_configs 表的外键 and 列
    op.drop_constraint("fk_project_model_configs_channel_id", "project_model_configs", type_="foreignkey")
    op.drop_column("project_model_configs", "channel_id")

    # 3. 删除 provider_channels 表
    op.drop_table("provider_channels")
