"""
0011 — Tabla price_months + trip_type nullable

Cambios:
  1. Nueva tabla price_months — precios mensuales por ruta enviados desde
     fetchMonthPriceCalendar en Android. Sirve como contexto de tendencia;
     no dispara alertas por sí sola.

  2. ALTER COLUMN trip_type → nullable en:
     - price_watches
     - price_snapshots
     - notification_queue
     El nuevo endpoint /events/price-calendar no envía trip_type.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS price_months (
            id           SERIAL PRIMARY KEY,
            user_id      TEXT        NOT NULL,
            origin       VARCHAR(10) NOT NULL,
            destination  VARCHAR(10) NOT NULL,
            year         INTEGER     NOT NULL,
            month        INTEGER     NOT NULL,
            price_raw    FLOAT       NOT NULL,
            price_category VARCHAR(50) NOT NULL,
            currency     VARCHAR(3)  NOT NULL,
            received_at  TIMESTAMP   NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_price_months_user_id
            ON price_months (user_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_price_months_route
            ON price_months (user_id, origin, destination, year, month)
    """)

    # Hacer trip_type nullable en tablas existentes
    op.execute("ALTER TABLE price_watches    ALTER COLUMN trip_type DROP NOT NULL")
    op.execute("ALTER TABLE price_snapshots  ALTER COLUMN trip_type DROP NOT NULL")
    op.execute("ALTER TABLE notification_queue ALTER COLUMN trip_type DROP NOT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS price_months")
    # No revertimos los NOT NULL — demasiado riesgo si ya hay filas NULL
