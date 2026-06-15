"""add provider_channel_models table for multi-model channels

Revision ID: 0012_add_provider_channel_models
Revises: 0011_add_channel_api_key
Create Date: 2026-04-09 15:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_add_provider_channel_models"
down_revision = "0011_add_channel_api_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 创建 provider_channel_models 表
    op.create_table(
        "provider_channel_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("provider_model_id", sa.String(length=200), nullable=True),
        sa.Column("owned_by", sa.String(length=100), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "model_name", name="uq_channel_model_name"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["provider_channels.id"],
            name="fk_provider_channel_models_channel_id",
            ondelete="CASCADE",
        ),
    )
    
    # 创建索引优化查询
    op.create_index(
        "ix_provider_channel_models_channel_id",
        "provider_channel_models",
        ["channel_id"],
    )
    op.create_index(
        "ix_provider_channel_models_is_enabled",
        "provider_channel_models",
        ["is_enabled"],
    )
    op.create_index(
        "ix_provider_channel_models_is_default",
        "provider_channel_models",
        ["is_default"],
    )


def downgrade() -> None:
    # 删除索引
    op.drop_index("ix_provider_channel_models_is_default", table_name="provider_channel_models")
    op.drop_index("ix_provider_channel_models_is_enabled", table_name="provider_channel_models")
    op.drop_index("ix_provider_channel_models_channel_id", table_name="provider_channel_models")
    # 删除表
    op.drop_table("provider_channel_models")
