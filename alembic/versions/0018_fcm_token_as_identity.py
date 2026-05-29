"""
0018 — FCM token como identidad real. user_id se vuelve nullable/legacy.

No existen cuentas de usuario. El FCM token es el único identificador del
dispositivo. user_id era un campo legacy que se usaba con valores anonymous_XXXX
o bien con el propio FCM token repetido. Ahora queda limpio:

  user_profiles.fcm_token  → NOT NULL, UNIQUE (identidad real)
  user_profiles.user_id    → nullable, reservado para autenticación futura

Pasos:
  1. Tablas hijas: reemplazar anonymous_XXXX con el fcm_token correspondiente
     (price_watches, notification_queue, search_events, campaign_sends,
      notification_log, impact_link_log)
  2. Nullear orphaned anonymous que no tengan fcm_token match
  3. user_profiles: cambiar PK a id SERIAL, hacer user_id nullable, nullear todo,
     poner fcm_token NOT NULL + UNIQUE
  4. Tablas hijas: hacer user_id nullable (ahora almacena fcm_token o NULL)
"""

from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # -------------------------------------------------------------------------
    # 1. Actualizar tablas hijas: anonymous_XXXX → fcm_token real
    # -------------------------------------------------------------------------

    # price_watches
    r = conn.execute(sa.text("""
        UPDATE price_watches pw
        SET user_id = up.fcm_token
        FROM user_profiles up
        WHERE pw.user_id = up.user_id
          AND up.fcm_token IS NOT NULL
          AND up.fcm_token != ''
          AND pw.user_id != up.fcm_token;
    """))
    print(f"[0018] price_watches migrados: {r.rowcount}")

    # notification_queue
    r = conn.execute(sa.text("""
        UPDATE notification_queue nq
        SET user_id = up.fcm_token
        FROM user_profiles up
        WHERE nq.user_id = up.user_id
          AND up.fcm_token IS NOT NULL
          AND up.fcm_token != ''
          AND nq.user_id != up.fcm_token;
    """))
    print(f"[0018] notification_queue migrados: {r.rowcount}")

    # search_events
    r = conn.execute(sa.text("""
        UPDATE search_events se
        SET user_id = up.fcm_token
        FROM user_profiles up
        WHERE se.user_id = up.user_id
          AND up.fcm_token IS NOT NULL
          AND up.fcm_token != ''
          AND se.user_id != up.fcm_token;
    """))
    print(f"[0018] search_events migrados: {r.rowcount}")

    # campaign_sends
    r = conn.execute(sa.text("""
        UPDATE campaign_sends cs
        SET user_id = up.fcm_token
        FROM user_profiles up
        WHERE cs.user_id = up.user_id
          AND up.fcm_token IS NOT NULL
          AND up.fcm_token != ''
          AND cs.user_id != up.fcm_token;
    """))
    print(f"[0018] campaign_sends migrados: {r.rowcount}")

    # notification_log (nullable, solo actualizar los que matchean)
    conn.execute(sa.text("""
        UPDATE notification_log nl
        SET user_id = up.fcm_token
        FROM user_profiles up
        WHERE nl.user_id = up.user_id
          AND up.fcm_token IS NOT NULL
          AND up.fcm_token != ''
          AND nl.user_id != up.fcm_token;
    """))

    # impact_link_log (nullable)
    conn.execute(sa.text("""
        UPDATE impact_link_log ill
        SET user_id = up.fcm_token
        FROM user_profiles up
        WHERE ill.user_id = up.user_id
          AND up.fcm_token IS NOT NULL
          AND up.fcm_token != ''
          AND ill.user_id != up.fcm_token;
    """))

    # -------------------------------------------------------------------------
    # 2. Nullear orphaned anonymous_XXXX sin match de fcm_token
    # -------------------------------------------------------------------------

    conn.execute(sa.text("""
        -- pending sin fcm_token → cancelar
        UPDATE notification_queue
        SET status = 'skipped', error_msg = 'no_fcm_after_migration'
        WHERE status = 'pending' AND user_id LIKE 'anonymous_%';
    """))

    for table in ["price_watches", "notification_queue", "search_events",
                  "campaign_sends", "notification_log", "impact_link_log"]:
        conn.execute(sa.text(f"""
            UPDATE {table} SET user_id = NULL
            WHERE user_id LIKE 'anonymous\\_%%' ESCAPE '\\';
        """))

    # -------------------------------------------------------------------------
    # 3. user_profiles: nueva PK + user_id nullable + fcm_token NOT NULL UNIQUE
    # -------------------------------------------------------------------------

    # Agregar nueva PK sintética
    conn.execute(sa.text("""
        ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS id SERIAL;
    """))

    # Sacar el PRIMARY KEY de user_id
    conn.execute(sa.text("""
        ALTER TABLE user_profiles DROP CONSTRAINT user_profiles_pkey;
    """))

    # Poner la nueva PK en id
    conn.execute(sa.text("""
        ALTER TABLE user_profiles ADD PRIMARY KEY (id);
    """))

    # Hacer user_id nullable
    conn.execute(sa.text("""
        ALTER TABLE user_profiles ALTER COLUMN user_id DROP NOT NULL;
    """))

    # Nullear user_id para todos (no tiene valor de negocio)
    r = conn.execute(sa.text("""
        UPDATE user_profiles SET user_id = NULL;
    """))
    print(f"[0018] user_profiles.user_id nulleados: {r.rowcount}")

    # fcm_token NOT NULL
    conn.execute(sa.text("""
        ALTER TABLE user_profiles ALTER COLUMN fcm_token SET NOT NULL;
    """))

    # Eliminar perfiles duplicados (mismo fcm_token) — guardar solo el más reciente
    r = conn.execute(sa.text("""
        DELETE FROM user_profiles
        WHERE id NOT IN (
            SELECT DISTINCT ON (fcm_token) id
            FROM user_profiles
            WHERE fcm_token IS NOT NULL
            ORDER BY fcm_token, updated_at DESC NULLS LAST
        )
        AND fcm_token IS NOT NULL;
    """))
    print(f"[0018] user_profiles duplicados eliminados: {r.rowcount}")

    # Unique constraint en fcm_token (ya había un índice parcial, ahora lo formalizamos)
    conn.execute(sa.text("""
        DROP INDEX IF EXISTS uix_user_profiles_fcm_token;
        ALTER TABLE user_profiles ADD CONSTRAINT uq_user_profiles_fcm_token UNIQUE (fcm_token);
    """))

    # -------------------------------------------------------------------------
    # 4. Hacer user_id nullable en tablas hijas (ahora almacena fcm_token o NULL)
    # -------------------------------------------------------------------------

    for table, col in [
        ("price_watches",     "user_id"),
        ("notification_queue","user_id"),
        ("search_events",     "user_id"),
        ("campaign_sends",    "user_id"),
    ]:
        conn.execute(sa.text(f"""
            ALTER TABLE {table} ALTER COLUMN {col} DROP NOT NULL;
        """))

    print("[0018] Migración completada.")


def downgrade() -> None:
    # No reversible de forma segura — los valores anonymous fueron borrados
    pass
