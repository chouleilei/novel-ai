"""add project auto_accept_on_max_retries setting

Revision ID: 0013_project_retry_auto_accept
Revises: 0012_add_provider_channel_models
Create Date: 2026-04-17 12:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_project_retry_auto_accept"
down_revision = "0012_add_provider_channel_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("auto_accept_on_max_retries", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("projects", "auto_accept_on_max_retries")
