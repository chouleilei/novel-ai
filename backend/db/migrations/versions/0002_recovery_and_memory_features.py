"""recovery and memory features

Revision ID: 0002_recovery_memory
Revises: 0001_initial
Create Date: 2026-03-26 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_recovery_memory"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("lease_owner", sa.String(length=100), nullable=True))
    op.add_column("projects", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column("projects", sa.Column("distant_memory_cache", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("distant_memory_updated_chapter", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "distant_memory_updated_chapter")
    op.drop_column("projects", "distant_memory_cache")
    op.drop_column("projects", "lease_expires_at")
    op.drop_column("projects", "lease_owner")
