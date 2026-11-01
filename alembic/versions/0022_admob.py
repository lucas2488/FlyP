"""
0022 — Tablas del dashboard de AdMob

Inventario (cuenta, apps, bloques), reportes diarios de red (principal, versiones,
personalización, tipo de anuncio), mediación, fotos horarias de "hoy" y registro de
corridas. Las llena app/services/admob_sync.py con el scope admob.readonly.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "admob_account",
        sa.Column("publisher_id", sa.String(32), primary_key=True),
        sa.Column("reporting_time_zone", sa.String(64)),
        sa.Column("currency", sa.String(8)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("synced_at", TZ, server_default=sa.func.now()),
    )
    op.create_table(
        "admob_apps",
        sa.Column("app_id", sa.String(64), primary_key=True),
        sa.Column("platform", sa.String(16)),
        sa.Column("name", sa.Text),
        sa.Column("app_store_id", sa.Text),
        sa.Column("approval_state", sa.String(32)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("synced_at", TZ, server_default=sa.func.now()),
    )
    op.create_table(
        "admob_ad_units",
        sa.Column("ad_unit_id", sa.String(64), primary_key=True),
        sa.Column("app_id", sa.String(64)),
        sa.Column("name", sa.Text),
        sa.Column("format", sa.String(32)),
        sa.Column("ad_types", JSONB, nullable=False),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("synced_at", TZ, server_default=sa.func.now()),
    )
    op.create_table(
        "admob_daily",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("app_id", sa.String(64), primary_key=True),
        sa.Column("ad_unit_id", sa.String(64), primary_key=True),
        sa.Column("format", sa.String(32), primary_key=True),
        sa.Column("platform", sa.String(16), primary_key=True),
        sa.Column("country", sa.String(8), primary_key=True),
        sa.Column("app_name", sa.Text),
        sa.Column("ad_unit_name", sa.Text),
        sa.Column("requests", sa.BigInteger, server_default="0"),
        sa.Column("matched", sa.BigInteger, server_default="0"),
        sa.Column("impressions", sa.BigInteger, server_default="0"),
        sa.Column("clicks", sa.BigInteger, server_default="0"),
        sa.Column("earnings", sa.Numeric(18, 6), server_default="0"),
        sa.Column("currency", sa.String(8)),
        sa.Column("synced_at", TZ, server_default=sa.func.now()),
    )
    op.create_table(
        "admob_daily_versions",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("dimension_type", sa.String(16), primary_key=True),
        sa.Column("app_id", sa.String(64), primary_key=True),
        sa.Column("platform", sa.String(16), primary_key=True),
        sa.Column("version", sa.String(64), primary_key=True),
        sa.Column("requests", sa.BigInteger, server_default="0"),
        sa.Column("matched", sa.BigInteger, server_default="0"),
        sa.Column("impressions", sa.BigInteger, server_default="0"),
        sa.Column("clicks", sa.BigInteger, server_default="0"),
        sa.Column("earnings", sa.Numeric(18, 6), server_default="0"),
        sa.Column("currency", sa.String(8)),
        sa.Column("synced_at", TZ, server_default=sa.func.now()),
    )
    op.create_table(
        "admob_daily_serving",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("app_id", sa.String(64), primary_key=True),
        sa.Column("platform", sa.String(16), primary_key=True),
        sa.Column("format", sa.String(32), primary_key=True),
        sa.Column("serving_restriction", sa.String(64), primary_key=True),
        sa.Column("requests", sa.BigInteger, server_default="0"),
        sa.Column("matched", sa.BigInteger, server_default="0"),
        sa.Column("impressions", sa.BigInteger, server_default="0"),
        sa.Column("clicks", sa.BigInteger, server_default="0"),
        sa.Column("earnings", sa.Numeric(18, 6), server_default="0"),
        sa.Column("currency", sa.String(8)),
        sa.Column("synced_at", TZ, server_default=sa.func.now()),
    )
    op.create_table(
        "admob_daily_ad_type",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("app_id", sa.String(64), primary_key=True),
        sa.Column("platform", sa.String(16), primary_key=True),
        sa.Column("format", sa.String(32), primary_key=True),
        sa.Column("ad_type", sa.String(32), primary_key=True),
        sa.Column("impressions", sa.BigInteger, server_default="0"),
        sa.Column("clicks", sa.BigInteger, server_default="0"),
        sa.Column("earnings", sa.Numeric(18, 6), server_default="0"),
        sa.Column("currency", sa.String(8)),
        sa.Column("synced_at", TZ, server_default=sa.func.now()),
    )
    op.create_table(
        "admob_mediation_daily",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("app_id", sa.String(64), primary_key=True),
        sa.Column("ad_source_id", sa.String(64), primary_key=True),
        sa.Column("mediation_group_id", sa.String(96), primary_key=True),
        sa.Column("format", sa.String(32), primary_key=True),
        sa.Column("platform", sa.String(16), primary_key=True),
        sa.Column("ad_source_name", sa.Text),
        sa.Column("mediation_group_name", sa.Text),
        sa.Column("requests", sa.BigInteger, server_default="0"),
        sa.Column("matched", sa.BigInteger, server_default="0"),
        sa.Column("impressions", sa.BigInteger, server_default="0"),
        sa.Column("clicks", sa.BigInteger, server_default="0"),
        sa.Column("earnings", sa.Numeric(18, 6), server_default="0"),
        sa.Column("observed_ecpm", sa.Numeric(18, 6)),
        sa.Column("ad_load_latency", sa.Float),
        sa.Column("currency", sa.String(8)),
        sa.Column("synced_at", TZ, server_default=sa.func.now()),
    )
    op.create_table(
        "admob_intraday_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("taken_at", TZ),
        sa.Column("report_date", sa.Date),
        sa.Column("app_id", sa.String(64)),
        sa.Column("platform", sa.String(16)),
        sa.Column("earnings", sa.Numeric(18, 6), server_default="0"),
        sa.Column("impressions", sa.BigInteger, server_default="0"),
        sa.Column("clicks", sa.BigInteger, server_default="0"),
    )
    op.create_table(
        "admob_sync_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32)),
        sa.Column("started_at", TZ, server_default=sa.func.now()),
        sa.Column("finished_at", TZ),
        sa.Column("status", sa.String(16)),
        sa.Column("range_from", sa.Date),
        sa.Column("range_to", sa.Date),
        sa.Column("rows", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("detail", JSONB),
    )
    op.create_index("ix_admob_ad_units_app_id", "admob_ad_units", ["app_id"])
    op.create_index("ix_admob_intraday_snapshots_taken_at", "admob_intraday_snapshots", ["taken_at"])
    op.create_index("ix_admob_intraday_snapshots_report_date", "admob_intraday_snapshots", ["report_date"])
    op.create_index("ix_admob_sync_runs_kind", "admob_sync_runs", ["kind"])


def downgrade() -> None:
    op.drop_table("admob_sync_runs")
    op.drop_table("admob_intraday_snapshots")
    op.drop_table("admob_mediation_daily")
    op.drop_table("admob_daily_ad_type")
    op.drop_table("admob_daily_serving")
    op.drop_table("admob_daily_versions")
    op.drop_table("admob_daily")
    op.drop_table("admob_ad_units")
    op.drop_table("admob_apps")
    op.drop_table("admob_account")
