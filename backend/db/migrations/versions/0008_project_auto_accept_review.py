"""add project auto-accept critic-failed setting

Revision ID: 0008_project_auto_accept_review
Revises: 0007_gen_jobs_active_uq
Create Date: 2026-04-09 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_project_auto_accept_review"
down_revision = "0007_gen_jobs_active_uq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("auto_accept_critic_failed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "chapters",
        sa.Column("auto_accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("chapters", "auto_accepted")
    op.drop_column("projects", "auto_accept_critic_failed")
