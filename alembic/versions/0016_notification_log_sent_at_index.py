"""
0016 — Índice en notification_log.sent_at para cleanup y consultas de historial

El job de limpieza (4am AR) borra registros de notification_log con más de 90
días. Sin este índice ese DELETE haría un full scan en una tabla que puede
tener cientos de miles de filas. El endpoint /analytics/notifications/history
también se beneficia porque filtra y ordena por sent_at.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_notification_log_sent_at
        ON notification_log (sent_at DESC);
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_notification_log_sent_at;
    """))
