"""multi-level notifications, opened tracking, reengagement, favorites

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-24

Cambios:
  - price_watches: 3 timestamps de cooldown por nivel (soft/strong/urgent)
  - notification_queue: drop_level, notification_type, opened_at
  - search_events: notified_reengagement_at
  - Nueva tabla flyp_user_favorites (nombre con prefijo para evitar colisión con n8n)

Nota: todos los ALTER TABLE usan IF NOT EXISTS para ser idempotentes.
La tabla flyp_user_favorites usa op.create_table que falla si ya existe,
pero como es nueva no hay conflicto.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- price_watches: cooldowns por nivel ---
    op.execute(sa.text("""
        ALTER TABLE price_watches
        ADD COLUMN IF NOT EXISTS last_notified_soft_at TIMESTAMP
    """))
    op.execute(sa.text("""
        ALTER TABLE price_watches
        ADD COLUMN IF NOT EXISTS last_notified_strong_at TIMESTAMP
    """))
    op.execute(sa.text("""
        ALTER TABLE price_watches
        ADD COLUMN IF NOT EXISTS last_notified_urgent_at TIMESTAMP
    """))

    # --- notification_queue: nivel, tipo y apertura ---
    op.execute(sa.text("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS drop_level VARCHAR(10)
    """))
    op.execute(sa.text("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS notification_type VARCHAR(20) DEFAULT 'price_drop'
    """))
    op.execute(sa.text("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS opened_at TIMESTAMP
    """))

    # --- search_events: re-engagement ---
    op.execute(sa.text("""
        ALTER TABLE search_events
        ADD COLUMN IF NOT EXISTS notified_reengagement_at TIMESTAMP
    """))

    # --- Nueva tabla flyp_user_favorites ---
    op.create_table(
        "flyp_user_favorites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("origin_iata", sa.String(10), nullable=False),
        sa.Column("destination_iata", sa.String(10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index("ix_flyp_user_favorites_user_id", "flyp_user_favorites", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_flyp_user_favorites_user_id", table_name="flyp_user_favorites")
    op.drop_table("flyp_user_favorites")
    op.execute(sa.text("ALTER TABLE search_events DROP COLUMN IF EXISTS notified_reengagement_at"))
    op.execute(sa.text("ALTER TABLE notification_queue DROP COLUMN IF EXISTS opened_at"))
    op.execute(sa.text("ALTER TABLE notification_queue DROP COLUMN IF EXISTS notification_type"))
    op.execute(sa.text("ALTER TABLE notification_queue DROP COLUMN IF EXISTS drop_level"))
    op.execute(sa.text("ALTER TABLE price_watches DROP COLUMN IF EXISTS last_notified_urgent_at"))
    op.execute(sa.text("ALTER TABLE price_watches DROP COLUMN IF EXISTS last_notified_strong_at"))
    op.execute(sa.text("ALTER TABLE price_watches DROP COLUMN IF EXISTS last_notified_soft_at"))
