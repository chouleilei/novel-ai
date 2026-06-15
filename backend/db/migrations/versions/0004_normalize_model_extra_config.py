"""normalize project model extra_config

Revision ID: 0004_normalize_model_extra_cfg
Revises: 0003_nullable_model_cfg
Create Date: 2026-03-27 00:00:04
"""

from alembic import op


revision = "0004_normalize_model_extra_cfg"
down_revision = "0003_nullable_model_cfg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE project_model_configs
        SET extra_config = '{}'::jsonb
        WHERE extra_config IS NULL;
        """
    )


def downgrade() -> None:
    pass
