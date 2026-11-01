"""
0021 — Tablas del reporte de afiliados de Impact

impact_programs, impact_actions, impact_clicks, impact_daily_performance,
impact_invoices, impact_click_exports e impact_sync_runs. Las llena
app/services/impact_sync.py con el token de solo lectura "Dashboard reportes".
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)
MONEY = sa.Numeric(14, 4)


def upgrade() -> None:
    op.create_table(
        "impact_programs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.Text),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("synced_at", TZ, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "impact_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("campaign_id", sa.String(32)),
        sa.Column("campaign_name", sa.Text),
        sa.Column("action_tracker_id", sa.String(32)),
        sa.Column("action_tracker_name", sa.Text),
        sa.Column("state", sa.String(16)),
        sa.Column("payout", MONEY),
        sa.Column("amount", MONEY),
        sa.Column("currency", sa.String(8)),
        sa.Column("event_date", TZ),
        sa.Column("locking_date", TZ),
        sa.Column("creation_date", TZ),
        sa.Column("cleared_date", TZ),
        sa.Column("referring_date", TZ),
        sa.Column("referring_type", sa.String(64)),
        sa.Column("referring_domain", sa.Text),
        sa.Column("sub_id1", sa.Text),
        sa.Column("sub_id2", sa.Text),
        sa.Column("sub_id3", sa.Text),
        sa.Column("shared_id", sa.Text),
        sa.Column("oid", sa.Text),
        sa.Column("delta_payout", MONEY),
        sa.Column("delta_amount", MONEY),
        sa.Column("intended_payout", MONEY),
        sa.Column("intended_amount", MONEY),
        sa.Column("ad_id", sa.String(64)),
        sa.Column("promo_code", sa.Text),
        sa.Column("event_code", sa.Text),
        sa.Column("customer_city", sa.Text),
        sa.Column("customer_region", sa.Text),
        sa.Column("customer_country", sa.String(64)),
        sa.Column("customer_area", sa.Text),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("synced_at", TZ, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_impact_actions_state", "impact_actions", ["state"])
    op.create_index("ix_impact_actions_event_date", "impact_actions", ["event_date"])
    op.create_index("ix_impact_actions_locking_date", "impact_actions", ["locking_date"])

    op.create_table(
        "impact_clicks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("export_date", sa.Date, nullable=False),
        sa.Column("external_id", sa.String(64)),
        sa.Column("event_date", TZ),
        sa.Column("program_id", sa.String(32)),
        sa.Column("program_name", sa.Text),
        sa.Column("ad_id", sa.String(64)),
        sa.Column("ad_name", sa.Text),
        sa.Column("ad_type", sa.String(64)),
        sa.Column("product_sku", sa.Text),
        sa.Column("media_id", sa.String(64)),
        sa.Column("shared_id", sa.Text),
        sa.Column("sub_id1", sa.Text),
        sa.Column("sub_id2", sa.Text),
        sa.Column("sub_id3", sa.Text),
        sa.Column("customer_country", sa.String(64)),
        sa.Column("customer_region", sa.Text),
        sa.Column("customer_city", sa.Text),
        sa.Column("customer_area", sa.Text),
        sa.Column("ip_hash", sa.Text),
        sa.Column("device_type", sa.String(64)),
        sa.Column("device_family", sa.Text),
        sa.Column("browser", sa.Text),
        sa.Column("os", sa.Text),
        sa.Column("referring_url", sa.Text),
        sa.Column("referring_domain", sa.Text),
        sa.Column("landing_page_url", sa.Text),
        sa.Column("traffic_source", sa.Text),
        sa.Column("traffic_category", sa.Text),
        sa.Column("unique_click", sa.Boolean),
        sa.Column("payout", MONEY),
        sa.Column("deal_name", sa.Text),
        sa.Column("deal_type", sa.Text),
        sa.Column("raw", JSONB, nullable=False),
    )
    op.create_index("ix_impact_clicks_export_date", "impact_clicks", ["export_date"])
    op.create_index("ix_impact_clicks_event_date", "impact_clicks", ["event_date"])

    op.create_table(
        "impact_daily_performance",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("clicks", sa.Integer),
        sa.Column("unique_clicks", sa.Integer),
        sa.Column("actions", sa.Integer),
        sa.Column("payout", MONEY),
        sa.Column("sale_amount", MONEY),
        sa.Column("currency", sa.String(8)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("synced_at", TZ, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "impact_invoices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_date", TZ),
        sa.Column("currency", sa.String(8)),
        sa.Column("total_amount", MONEY),
        sa.Column("total_vat_amount", MONEY),
        sa.Column("status", sa.String(16)),
        sa.Column("due_date", TZ),
        sa.Column("paid_date", TZ),
        sa.Column("line_items", JSONB, nullable=False),
        sa.Column("detailed_line_items", JSONB, nullable=False),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("synced_at", TZ, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_impact_invoices_created_date", "impact_invoices", ["created_date"])

    op.create_table(
        "impact_click_exports",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("job_id", sa.String(64)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rows", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("requested_at", TZ),
        sa.Column("completed_at", TZ),
    )

    op.create_table(
        "impact_sync_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("started_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", TZ),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("rows", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("detail", JSONB),
    )
    op.create_index("ix_impact_sync_runs_kind", "impact_sync_runs", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_impact_sync_runs_kind", table_name="impact_sync_runs")
    op.drop_table("impact_sync_runs")
    op.drop_table("impact_click_exports")
    op.drop_index("ix_impact_invoices_created_date", table_name="impact_invoices")
    op.drop_table("impact_invoices")
    op.drop_table("impact_daily_performance")
    op.drop_index("ix_impact_clicks_event_date", table_name="impact_clicks")
    op.drop_index("ix_impact_clicks_export_date", table_name="impact_clicks")
    op.drop_table("impact_clicks")
    op.drop_index("ix_impact_actions_locking_date", table_name="impact_actions")
    op.drop_index("ix_impact_actions_event_date", table_name="impact_actions")
    op.drop_index("ix_impact_actions_state", table_name="impact_actions")
    op.drop_table("impact_actions")
    op.drop_table("impact_programs")
