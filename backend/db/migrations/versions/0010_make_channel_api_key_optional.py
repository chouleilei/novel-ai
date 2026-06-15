"""make channel api_key_env_var optional

Revision ID: 0010_make_channel_api_key_optional
Revises: 0009_add_provider_channels
Create Date: 2026-04-09 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010_channel_key_optional"
down_revision = "0009_add_provider_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 修改 api_key_env_var 列为可为空
    op.alter_column(
        "provider_channels",
        "api_key_env_var",
        existing_type=sa.String(length=100),
        nullable=True,
    )


def downgrade() -> None:
    # 恢复为不可为空
    op.alter_column(
        "provider_channels",
        "api_key_env_var",
        existing_type=sa.String(length=100),
        nullable=False,
    )
