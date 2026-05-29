"""
0015 — Fix app name in notification templates + add retention index on notification_queue

Cambios:
  1. Corrige el template AR de bienvenida que decía "FlyP" → "Fly Promociones"
  2. Agrega índice compuesto (status, created_at) en notification_queue para
     acelerar el job de limpieza de items viejos.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Corregir "FlyP" → "Fly Promociones" en el template de bienvenida AR
    op.execute(sa.text("""
        UPDATE notification_templates
        SET body_template = 'Ahora recibís alertas de vuelos baratos. ¡Ahorrá en tu próximo viaje con Fly Promociones!'
        WHERE drop_level = 'welcome'
          AND country_code = 'AR'
          AND body_template LIKE '%FlyP%';
    """))

    # 2. Índice para el cleanup job (evita full scan en tabla grande)
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_notification_queue_cleanup
        ON notification_queue (status, created_at)
        WHERE status IN ('sent', 'failed', 'skipped');
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_notification_queue_cleanup;

        UPDATE notification_templates
        SET body_template = 'Ahora recibís alertas de vuelos baratos. ¡Ahorrá en tu próximo viaje con FlyP!'
        WHERE drop_level = 'welcome'
          AND country_code = 'AR'
          AND body_template LIKE '%Fly Promociones%';
    """))
