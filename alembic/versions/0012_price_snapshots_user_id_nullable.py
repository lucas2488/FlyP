"""
0012 — user_id nullable en price_snapshots y price_months

Los precios son datos de RUTA, no de usuario.
price-calendar y month-calendar ya no reciben identificador de usuario,
por lo que el user_id en estas tablas debe ser opcional (NULL).
"""

from typing import Union
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # price_snapshots.user_id — antes NOT NULL (identificaba al usuario que buscó)
    # ahora NULL — los snapshots son datos de ruta sin dueño
    op.execute("ALTER TABLE price_snapshots ALTER COLUMN user_id DROP NOT NULL")

    # price_months.user_id — el modelo ya era nullable pero la migración 0011
    # lo creó como NOT NULL en la DB. Corregimos la inconsistencia.
    op.execute("ALTER TABLE price_months ALTER COLUMN user_id DROP NOT NULL")


def downgrade() -> None:
    # No revertimos — si ya hay filas NULL volver a NOT NULL rompería la DB
    pass
