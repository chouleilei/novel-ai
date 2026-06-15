"""add project hard review gates setting

Revision ID: 0014_project_hard_review_gates
Revises: 0013_project_retry_auto_accept
Create Date: 2026-04-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_project_hard_review_gates"
down_revision = "0013_project_retry_auto_accept"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("hard_review_gates_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("projects", "hard_review_gates_enabled")
