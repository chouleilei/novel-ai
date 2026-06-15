"""allow nullable model temperature and max tokens

Revision ID: 0003_nullable_model_cfg
Revises: 0002_recovery_and_memory_features
Create Date: 2026-03-26 00:00:03
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_nullable_model_cfg"
down_revision = "0002_recovery_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("project_model_configs", "temperature", existing_type=sa.Numeric(3, 2), nullable=True)
    op.alter_column("project_model_configs", "max_tokens", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("project_model_configs", "max_tokens", existing_type=sa.Integer(), nullable=False)
    op.alter_column("project_model_configs", "temperature", existing_type=sa.Numeric(3, 2), nullable=False)
