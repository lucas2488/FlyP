"""add trip_type to flyp_user_favorites

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'flyp_user_favorites',
        sa.Column('trip_type', sa.String(20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('flyp_user_favorites', 'trip_type')
