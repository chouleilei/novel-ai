"""add project generation mode settings

Revision ID: 0015_project_generation_mode
Revises: 0014_project_hard_review_gates
Create Date: 2026-04-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_project_generation_mode"
down_revision = "0014_project_hard_review_gates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("generation_mode", sa.String(length=30), nullable=False, server_default=sa.text("'standard'")),
    )
    op.add_column(
        "projects",
        sa.Column("rush_previous_chapter_count", sa.Integer(), nullable=False, server_default=sa.text("10")),
    )


def downgrade() -> None:
    op.drop_column("projects", "rush_previous_chapter_count")
    op.drop_column("projects", "generation_mode")
