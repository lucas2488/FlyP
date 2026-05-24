"""campaigns, campaign_sends, campaign_calendar

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-24

Crea todas las tablas del sistema de campañas en una sola migración.
Incluye seed de slots semanales (Lun/Mié/Vie 10am AR) y fechas especiales AR.
Patrón idéntico a migraciones anteriores: SQL crudo con IF NOT EXISTS.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Tabla: campaigns
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id                  SERIAL PRIMARY KEY,
            name                VARCHAR(200) NOT NULL,
            segment             VARCHAR(50),
            status              VARCHAR(20) NOT NULL DEFAULT 'draft',
            scheduled_at        TIMESTAMP,
            sent_at             TIMESTAMP,
            campaign_type       VARCHAR(30),
            route_origin        VARCHAR(10),
            route_destination   VARCHAR(10),
            category_tag        VARCHAR(100),
            created_at          TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_campaigns_status_scheduled
        ON campaigns (status, scheduled_at)
    """))

    # -------------------------------------------------------------------------
    # Tabla: campaign_sends
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS campaign_sends (
            id              SERIAL PRIMARY KEY,
            campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
            user_id         VARCHAR NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            sent_at         TIMESTAMP,
            opened_at       TIMESTAMP,
            fcm_response    TEXT,
            created_at      TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_campaign_sends_campaign_user
        ON campaign_sends (campaign_id, user_id)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_campaign_sends_user_sent
        ON campaign_sends (user_id, sent_at)
    """))

    # -------------------------------------------------------------------------
    # Tabla: campaign_calendar
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS campaign_calendar (
            id              SERIAL PRIMARY KEY,
            slot_type       VARCHAR(20) NOT NULL,    -- 'weekly' | 'special'
            day_of_week     INTEGER,                 -- 0=Lun .. 6=Dom (para weekly)
            send_hour_ar    INTEGER NOT NULL DEFAULT 10,
            special_date    DATE,                    -- fecha exacta (para special)
            label           VARCHAR(200),
            advance_days    INTEGER NOT NULL DEFAULT 0,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE
        )
    """))

    # -------------------------------------------------------------------------
    # Seed: slots semanales — Lunes / Miércoles / Viernes a las 10am AR
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        INSERT INTO campaign_calendar
            (slot_type, day_of_week, send_hour_ar, label, advance_days, is_active)
        VALUES
            ('weekly', 0, 10, 'Lunes 10am',     0, TRUE),
            ('weekly', 2, 10, 'Miércoles 10am', 0, TRUE),
            ('weekly', 4, 10, 'Viernes 10am',   0, TRUE)
    """))

    # -------------------------------------------------------------------------
    # Seed: fechas especiales Argentina 2026 / 2027
    # advance_days = días antes de la fecha en que se notifica
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        INSERT INTO campaign_calendar
            (slot_type, send_hour_ar, special_date, label, advance_days, is_active)
        VALUES
            ('special', 9, '2026-02-16', 'Carnaval 2026',             3, TRUE),
            ('special', 9, '2026-04-02', 'Semana Santa 2026',         3, TRUE),
            ('special', 9, '2026-05-18', 'Hot Sale 2026',             3, TRUE),
            ('special', 9, '2026-07-18', 'Vacaciones Invierno 2026',  3, TRUE),
            ('special', 9, '2026-10-12', 'Feriado 12 Octubre 2026',   2, TRUE),
            ('special', 9, '2026-11-02', 'CyberMonday 2026',          3, TRUE),
            ('special', 9, '2026-12-26', 'Post Navidad 2026',         2, TRUE),
            ('special', 9, '2027-01-16', 'Verano AR 2027',            3, TRUE),
            ('special', 9, '2027-02-01', 'Carnaval 2027',             3, TRUE),
            ('special', 9, '2027-04-15', 'Semana Santa 2027',         3, TRUE),
            ('special', 9, '2027-05-17', 'Hot Sale 2027',             3, TRUE),
            ('special', 9, '2027-07-17', 'Vacaciones Invierno 2027',  3, TRUE),
            ('special', 9, '2027-11-01', 'CyberMonday 2027',          3, TRUE)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_campaign_sends_user_sent"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_campaign_sends_campaign_user"))
    op.execute(sa.text("DROP TABLE IF EXISTS campaign_sends"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_campaigns_status_scheduled"))
    op.execute(sa.text("DROP TABLE IF EXISTS campaigns"))
    op.execute(sa.text("DROP TABLE IF EXISTS campaign_calendar"))
