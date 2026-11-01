"""
0026 — Ruta y mercado en price_alert_notifications

Foto de la ruta y del país de la alerta al momento del aviso, para mostrarlo en el
dashboard aunque el usuario después borre la alerta.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_alert_notifications", sa.Column("route", sa.String(160)))
    op.add_column("price_alert_notifications", sa.Column("country", sa.String(8)))


def downgrade() -> None:
    op.drop_column("price_alert_notifications", "country")
    op.drop_column("price_alert_notifications", "route")
