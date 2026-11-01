"""
0028 — Búsquedas de precio hechas por los celulares (crowd checker)

price_alert_claims: reservas de búsquedas (una 'open' por search_key: índice único parcial).
price_alert_verifications: bajadas raras esperando confirmación (doble verificación).
app_settings: ajustes editables desde el dashboard.
price_alert_checks: origen (admin | app), aparato, reserva, precio de referencia, bajada y
si fue "rara". Ver DESIGN_crowd_checker.md.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "price_alert_claims",
        sa.Column("claim_id", sa.String(64), primary_key=True),
        sa.Column("search_key", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("device_id", sa.String(128)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("purpose", sa.String(16), nullable=False, server_default="check"),
        sa.Column("claimed_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("finished_at", TZ),
    )
    op.create_index("ix_price_alert_claims_search_key", "price_alert_claims", ["search_key"])
    op.create_index("ix_price_alert_claims_device_id", "price_alert_claims", ["device_id"])
    op.create_index("ix_price_alert_claims_claimed_at", "price_alert_claims", ["claimed_at"])
    # Una sola reserva abierta por búsqueda: es lo que hace atómico el reparto.
    op.create_index("ux_price_alert_claims_open_search", "price_alert_claims", ["search_key"],
                    unique=True, postgresql_where=sa.text("status = 'open'"))

    op.create_table(
        "price_alert_verifications",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("search_key", sa.String(32), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(8)),
        sa.Column("reference_price", sa.Numeric(18, 4)),
        sa.Column("itinerary_id", sa.String(256)),
        sa.Column("device_id", sa.String(128)),
        sa.Column("source", sa.String(16)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("resolved_price", sa.Numeric(18, 4)),
        sa.Column("resolved_by", sa.String(128)),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", TZ),
    )
    op.create_index("ix_price_alert_verifications_search_key", "price_alert_verifications", ["search_key"])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB),
        sa.Column("updated_at", TZ, server_default=sa.func.now(), nullable=False),
    )

    for name, typ in (("source", sa.String(16)), ("device_id", sa.String(128)), ("claim_id", sa.String(64)),
                      ("reference_price", sa.Numeric(18, 4)), ("drop_pct", sa.Float), ("rare", sa.Boolean),
                      ("verification", sa.String(16))):
        op.add_column("price_alert_checks", sa.Column(name, typ))
    op.create_index("ix_price_alert_checks_device_id", "price_alert_checks", ["device_id"])
    op.execute("UPDATE price_alert_checks SET source = 'admin' WHERE source IS NULL")


def downgrade() -> None:
    op.drop_index("ix_price_alert_checks_device_id", table_name="price_alert_checks")
    for name in ("verification", "rare", "drop_pct", "reference_price", "claim_id", "device_id", "source"):
        op.drop_column("price_alert_checks", name)
    op.drop_table("app_settings")
    op.drop_table("price_alert_verifications")
    op.drop_index("ux_price_alert_claims_open_search", table_name="price_alert_claims")
    op.drop_table("price_alert_claims")
