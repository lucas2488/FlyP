"""0029 — Pausa automática de alertas (desinstaló / no mira los avisos) y lectura de avisos

Revision ID: 0029
Revises: 0028
"""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_alert_notifications", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "price_alert_pauses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("alert_path", sa.String(256), nullable=False),
        sa.Column("uid", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("detail", sa.String(300)),
        sa.Column("route", sa.String(160)),
        sa.Column("paused_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_price_alert_pauses_alert_path", "price_alert_pauses", ["alert_path"])
    op.create_index("ix_price_alert_pauses_uid", "price_alert_pauses", ["uid"])
    op.create_index("ix_price_alert_pauses_paused_at", "price_alert_pauses", ["paused_at"])


def downgrade() -> None:
    op.drop_table("price_alert_pauses")
    op.drop_column("price_alert_notifications", "read_at")
