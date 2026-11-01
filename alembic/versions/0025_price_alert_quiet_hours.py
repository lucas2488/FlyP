"""
0025 — Horario de silencio de los avisos de precio

price_alert_notifications: uid, estado del push (sent | scheduled | cancelled | failed),
cuándo tiene que salir (push_due_at, de 22 a 9 hora local espera hasta las 9) y cuándo salió.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_alert_notifications", sa.Column("uid", sa.String(128)))
    op.add_column("price_alert_notifications", sa.Column("push_status", sa.String(16)))
    op.add_column("price_alert_notifications", sa.Column("push_due_at", sa.DateTime(timezone=True)))
    op.add_column("price_alert_notifications", sa.Column("push_sent_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE price_alert_notifications SET push_status = 'sent', push_sent_at = created_at")
    op.create_index("ix_price_alert_notifications_push_status", "price_alert_notifications", ["push_status"])


def downgrade() -> None:
    op.drop_index("ix_price_alert_notifications_push_status", table_name="price_alert_notifications")
    for c in ("push_sent_at", "push_due_at", "push_status", "uid"):
        op.drop_column("price_alert_notifications", c)
