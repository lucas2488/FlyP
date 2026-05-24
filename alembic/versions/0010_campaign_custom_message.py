"""
0010 — Mensaje personalizado + topic broadcast en campaigns

Los borradores de feriados/efemérides necesitan:
  1. Persistir su mensaje temático (custom_title + custom_body)
     para que el engine lo use en lugar de un template genérico.
  2. Saber a qué FCM topic enviarlo (target_topic):
     - Especial/feriado → "flypromociones_AR" (broadcast a todos los suscriptores)
     - Semanal          → NULL (envío per-user con ruta personalizada)

Regla del engine:
  target_topic IS NOT NULL → firebase_service.send_to_topic()  (1 llamada FCM)
  target_topic IS NULL     → loop por usuarios del segmento    (N llamadas FCM)
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE campaigns
            ADD COLUMN IF NOT EXISTS custom_title  VARCHAR(300),
            ADD COLUMN IF NOT EXISTS custom_body   TEXT,
            ADD COLUMN IF NOT EXISTS target_topic  VARCHAR(100);
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE campaigns
            DROP COLUMN IF EXISTS custom_title,
            DROP COLUMN IF EXISTS custom_body,
            DROP COLUMN IF EXISTS target_topic;
    """))
