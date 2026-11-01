"""
0023 — Historial de alertas de precio

price_alert_checks: cada precio que reporta el buscador (POST /checker/results).
price_alert_notifications: cada aviso de bajada creado en Firestore y su push.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "price_alert_checks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("search_key", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(64)),
        sa.Column("price", sa.Numeric(18, 4)),
        sa.Column("currency", sa.String(8)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("alerts_updated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("alerts_notified", sa.Integer, nullable=False, server_default="0"),
        sa.Column("checked_at", TZ, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_price_alert_checks_search_key", "price_alert_checks", ["search_key"])
    op.create_index("ix_price_alert_checks_checked_at", "price_alert_checks", ["checked_at"])

    op.create_table(
        "price_alert_notifications",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("alert_path", sa.String(160), nullable=False),
        sa.Column("notice_id", sa.String(64), nullable=False),
        sa.Column("search_key", sa.String(32), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("previous_price", sa.Numeric(18, 4)),
        sa.Column("target_price", sa.Numeric(18, 4)),
        sa.Column("currency", sa.String(8)),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.Column("devices", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pushes_sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("devices_removed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("push_errors", JSONB),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_price_alert_notifications_alert_path", "price_alert_notifications", ["alert_path"])
    op.create_index("ix_price_alert_notifications_created_at", "price_alert_notifications", ["created_at"])


def downgrade() -> None:
    op.drop_table("price_alert_notifications")
    op.drop_table("price_alert_checks")
