"""
0017 — Limpieza de user_ids duplicados por FCM token

El FCM token es la clave real de identidad. Un mismo dispositivo acumuló N
user_ids distintos (anonymous_XXXX de sesiones viejas) apuntando al mismo
FCM token. Cada uno tenía price_watches activos → el mismo teléfono recibía
N notificaciones idénticas por cada bajada de precio.

Resultado: 3.023 FCM tokens con duplicados, 9.895 price_watches sobrantes,
790 notificaciones pending espurias.

Este script:
  1. Por cada FCM token duplicado, elige el user_id canónico:
       - Prefiere user_id == fcm_token (registro nuevo-estilo, sin userId legacy)
       - Si no existe, toma el más recientemente actualizado
  2. Desactiva price_watches de todos los user_ids no canónicos
  3. Cancela notificaciones pending de esos user_ids
  No elimina user_profiles (integridad referencial con otras tablas).
  El índice único en fcm_token se maneja en la capa de código (profile.py).
"""

from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels = None
depends_on = None

_CANONICAL_CTE = """
    WITH canonical AS (
        SELECT DISTINCT ON (fcm_token)
            fcm_token,
            user_id AS canonical_user_id
        FROM user_profiles
        WHERE fcm_token IS NOT NULL
        ORDER BY
            fcm_token,
            (CASE WHEN user_id = fcm_token THEN 0 ELSE 1 END),
            updated_at DESC NULLS LAST
    ),
    duplicates AS (
        SELECT up.user_id
        FROM user_profiles up
        JOIN canonical c ON c.fcm_token = up.fcm_token
        WHERE up.user_id != c.canonical_user_id
    )
"""


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Desactivar price_watches de user_ids no canónicos
    result = conn.execute(sa.text(
        _CANONICAL_CTE +
        "UPDATE price_watches SET is_active = false "
        "WHERE user_id IN (SELECT user_id FROM duplicates) AND is_active = true "
        "RETURNING id;"
    ))
    watches_fixed = result.rowcount

    # 2. Cancelar notificaciones pending de esos user_ids
    result = conn.execute(sa.text(
        _CANONICAL_CTE +
        "UPDATE notification_queue "
        "SET status = 'skipped', error_msg = 'duplicate_fcm_cleaned' "
        "WHERE status = 'pending' "
        "  AND user_id IN (SELECT user_id FROM duplicates) "
        "RETURNING id;"
    ))
    notifs_fixed = result.rowcount

    print(f"[0017] price_watches desactivados: {watches_fixed}")
    print(f"[0017] notificaciones pending canceladas: {notifs_fixed}")


def downgrade() -> None:
    # No reversible — no podemos saber cuáles watches estaban activos antes
    pass
