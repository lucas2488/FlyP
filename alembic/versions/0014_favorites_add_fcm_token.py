"""add fcm_token to flyp_user_favorites + make user_id nullable

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Agregar columna fcm_token (identificador real del dispositivo)
    op.add_column(
        'flyp_user_favorites',
        sa.Column('fcm_token', sa.Text(), nullable=True)
    )
    # Hacer user_id nullable — no hay login todavía, puede ser NULL
    op.alter_column(
        'flyp_user_favorites', 'user_id',
        existing_type=sa.String(),
        nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        'flyp_user_favorites', 'user_id',
        existing_type=sa.String(),
        nullable=False
    )
    op.drop_column('flyp_user_favorites', 'fcm_token')
