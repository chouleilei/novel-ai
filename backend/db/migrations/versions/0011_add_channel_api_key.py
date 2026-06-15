"""add channel api_key field

Revision ID: 0011_add_channel_api_key
Revises: 0010_channel_key_optional
Create Date: 2026-04-09 12:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011_add_channel_api_key"
down_revision = "0010_channel_key_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加 api_key 列
    op.add_column(
        "provider_channels",
        sa.Column("api_key", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # 删除 api_key 列
    op.drop_column("provider_channels", "api_key")
