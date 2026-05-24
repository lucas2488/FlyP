"""multi-level notifications, opened tracking, reengagement, favorites

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-24

Cambios:
  - price_watches: 3 timestamps de cooldown por nivel (soft/strong/urgent)
  - notification_queue: drop_level, notification_type, opened_at
  - search_events: notified_reengagement_at
  - Nueva tabla user_favorites
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- price_watches: cooldowns por nivel ---
    op.execute("""
        ALTER TABLE price_watches
        ADD COLUMN IF NOT EXISTS last_notified_soft_at TIMESTAMP
    """)
    op.execute("""
        ALTER TABLE price_watches
        ADD COLUMN IF NOT EXISTS last_notified_strong_at TIMESTAMP
    """)
    op.execute("""
        ALTER TABLE price_watches
        ADD COLUMN IF NOT EXISTS last_notified_urgent_at TIMESTAMP
    """)

    # --- notification_queue: nivel, tipo y apertura ---
    op.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS drop_level VARCHAR(10)
    """)
    op.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS notification_type VARCHAR(20) DEFAULT 'price_drop'
    """)
    op.execute("""
        ALTER TABLE notification_queue
        ADD COLUMN IF NOT EXISTS opened_at TIMESTAMP
    """)

    # --- search_events: re-engagement ---
    op.execute("""
        ALTER TABLE search_events
        ADD COLUMN IF NOT EXISTS notified_reengagement_at TIMESTAMP
    """)

    # --- Nueva tabla user_favorites ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_favorites (
            id               SERIAL PRIMARY KEY,
            user_id          VARCHAR NOT NULL,
            origin_iata      VARCHAR(10) NOT NULL,
            destination_iata VARCHAR(10) NOT NULL,
            created_at       TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_user_favorites_user_id
        ON user_favorites (user_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_favorites")
    op.execute("ALTER TABLE search_events DROP COLUMN IF EXISTS notified_reengagement_at")
    op.execute("ALTER TABLE notification_queue DROP COLUMN IF EXISTS opened_at")
    op.execute("ALTER TABLE notification_queue DROP COLUMN IF EXISTS notification_type")
    op.execute("ALTER TABLE notification_queue DROP COLUMN IF EXISTS drop_level")
    op.execute("ALTER TABLE price_watches DROP COLUMN IF EXISTS last_notified_urgent_at")
    op.execute("ALTER TABLE price_watches DROP COLUMN IF EXISTS last_notified_strong_at")
    op.execute("ALTER TABLE price_watches DROP COLUMN IF EXISTS last_notified_soft_at")
