"""add segmentation fields to user_profiles

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-24

Agrega campos de segmentación calculados por el app Android:
  - user_segment: tipo de usuario (heavy_searcher, casual, inactive, etc.)
  - engagement_score: puntaje numérico de engagement (0.0 - 100.0)

El app Android calcula estos valores localmente en UserProfile.kt
y los envía en cada POST /api/v1/profile.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS hace la migración idempotente (segura de correr varias veces)
    op.execute("""
        ALTER TABLE user_profiles
        ADD COLUMN IF NOT EXISTS user_segment VARCHAR(50)
    """)
    op.execute("""
        ALTER TABLE user_profiles
        ADD COLUMN IF NOT EXISTS engagement_score FLOAT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS engagement_score")
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS user_segment")
