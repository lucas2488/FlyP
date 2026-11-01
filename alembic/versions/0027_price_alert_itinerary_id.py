"""
0027 — Id del vuelo (itinerario de Skyscanner) en los precios y avisos

Opcional: el buscador puede mandar el id del itinerario del precio más barato (el mismo
tipo de id que guarda el share como originalItineraryId).
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_alert_checks", sa.Column("itinerary_id", sa.String(256)))
    op.add_column("price_alert_notifications", sa.Column("itinerary_id", sa.String(256)))


def downgrade() -> None:
    op.drop_column("price_alert_notifications", "itinerary_id")
    op.drop_column("price_alert_checks", "itinerary_id")
