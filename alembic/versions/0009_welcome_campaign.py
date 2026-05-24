"""
0009 — Welcome campaign: scheduled_at en notification_queue + templates de bienvenida

Cambios:
  1. ALTER TABLE notification_queue ADD COLUMN scheduled_at TIMESTAMP
     Para soportar notificaciones con delay (welcome = now + 24h).
     Existentes tienen scheduled_at=NULL → se procesan igual que antes.
  2. Seed de 6 templates welcome en notification_templates (AR×2, MX, CO, BR, *)
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        -- 1. Columna scheduled_at para delayed notifications
        ALTER TABLE notification_queue
            ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP;

        -- 2. Templates de bienvenida (drop_level = 'welcome')
        INSERT INTO notification_templates
            (country_code, drop_level, title_template, body_template, is_active)
        VALUES
          ('AR', 'welcome',
           '¡Bienvenido a FlyPromociones! ✈️',
           '¡Ya activamos tus alertas de precio! Te avisamos cuando baje tu vuelo preferido.',
           TRUE),
          ('AR', 'welcome',
           '¡Listo para despegar! 🎉',
           'Ahora recibís alertas de vuelos baratos. ¡Ahorrá en tu próximo viaje con FlyP!',
           TRUE),
          ('MX', 'welcome',
           '¡Bienvenido a FlyPromociones! ✈️',
           '¡Activamos tus alertas de vuelo! Te avisamos cuando los precios bajen.',
           TRUE),
          ('CO', 'welcome',
           '¡Bienvenido a FlyPromociones! ✈️',
           '¡Ya tenés alertas de vuelos activadas! Volá más barato con FlyPromociones.',
           TRUE),
          ('BR', 'welcome',
           'Bem-vindo ao FlyPromociones! ✈️',
           'Ativamos seus alertas de passagens aéreas. Avisaremos quando os preços caírem!',
           TRUE),
          ('*', 'welcome',
           'Welcome to FlyPromociones! ✈️',
           'Your flight price alerts are now active. We''ll notify you when prices drop!',
           TRUE);
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE notification_queue DROP COLUMN IF EXISTS scheduled_at;
        DELETE FROM notification_templates WHERE drop_level = 'welcome';
    """))
