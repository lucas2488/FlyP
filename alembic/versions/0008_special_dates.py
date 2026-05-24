"""special_dates table + seed feriados y fechas comerciales AR 2026

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-24

Nueva tabla `special_dates` con metadata rica para el generador de campañas:
  - tipo: 'feriado' | 'comercial' | 'vacaciones'
  - anticipacion_dias: cuántos días antes disparar la notificación
  - mensaje_sugerido: texto de ayuda para el creador de campañas
  - activo: flag para habilitar/deshabilitar sin borrar

Diferencia con campaign_calendar (que ya existe):
  campaign_calendar = cuándo ejecutar (scheduling slots Lun/Mié/Vie + trigger dates)
  special_dates     = qué fechas son especiales + metadata de contenido

Seed incluye:
  - Feriados nacionales AR 2026 (inamovibles + trasladables)
  - Fechas comerciales: Hot Sale, CyberMonday, Día de la Madre, Navidad, etc.
  - Vacaciones: invierno, verano
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Tabla: special_dates
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS special_dates (
            id                  SERIAL PRIMARY KEY,
            nombre              VARCHAR(200) NOT NULL,
            fecha               DATE NOT NULL,
            tipo                VARCHAR(30) NOT NULL DEFAULT 'feriado',
            anticipacion_dias   INTEGER NOT NULL DEFAULT 3,
            mensaje_sugerido    TEXT,
            activo              BOOLEAN NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_special_dates_fecha
        ON special_dates (fecha)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_special_dates_activo
        ON special_dates (activo)
    """))

    # -------------------------------------------------------------------------
    # Seed: feriados nacionales AR 2026 (inamovibles)
    # Fuente: Decreto PEN + nolaborables.com.ar
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        INSERT INTO special_dates
            (nombre, fecha, tipo, anticipacion_dias, mensaje_sugerido, activo)
        VALUES
            ('Año Nuevo 2026',
             '2026-01-01', 'feriado', 3,
             '¡Feliz Año Nuevo! Empezá el 2026 viajando ✈️', TRUE),

            ('Carnaval — Lunes',
             '2026-02-16', 'feriado', 4,
             'Semana de Carnaval: ¡el mejor momento para escaparte! 🎉', TRUE),

            ('Carnaval — Martes',
             '2026-02-17', 'feriado', 4,
             '¡Carnaval! Aprovechá el feriado largo para volar ✈️🎊', TRUE),

            ('Día de la Memoria',
             '2026-03-24', 'feriado', 3,
             'Fin de semana largo — ideál para un escape corto 🛫', TRUE),

            ('Viernes Santo',
             '2026-04-03', 'feriado', 5,
             'Semana Santa: escapate antes de que se llenen los vuelos 🙏✈️', TRUE),

            ('Semana Santa',
             '2026-04-02', 'vacaciones', 7,
             '¡Semana Santa se viene! Buscá tu vuelo ahora y viajá tranquilo ✈️', TRUE),

            ('Día del Trabajador',
             '2026-05-01', 'feriado', 3,
             'Feriado del 1° de mayo — ¿aprovechás el fin de semana largo? ✈️', TRUE),

            ('Día de la Revolución de Mayo',
             '2026-05-25', 'feriado', 5,
             '25 de Mayo con feriado puente — escapate de largo fin de semana 🇦🇷✈️', TRUE),

            ('Día de la Bandera',
             '2026-06-20', 'feriado', 4,
             '20 de junio: feriado para volar 🇦🇷✈️', TRUE),

            ('Independencia Argentina',
             '2026-07-09', 'feriado', 5,
             '9 de Julio con feriado largo — ¡es hora de volar! 🇦🇷✈️', TRUE),

            ('Vacaciones de Invierno',
             '2026-07-18', 'vacaciones', 7,
             'Vacaciones de invierno: ¡viajá con toda la familia! ✈️❄️', TRUE),

            ('Día del Libertador San Martín',
             '2026-08-17', 'feriado', 3,
             'Feriado 17 de agosto — buen momento para un escapada ✈️', TRUE),

            ('Día de la Raza',
             '2026-10-12', 'feriado', 4,
             '12 de Octubre con feriado puente — ¡volá! ✈️🌎', TRUE),

            ('Día de la Soberanía Nacional',
             '2026-11-23', 'feriado', 4,
             'Feriado de la Soberanía: último largo del año ✈️ ¡aprovechalo!', TRUE),

            ('Inmaculada Concepción',
             '2026-12-08', 'feriado', 3,
             '8 de diciembre con fin de semana largo — ¡el último escapada antes de fin de año! ✈️', TRUE),

            ('Navidad 2026',
             '2026-12-25', 'feriado', 7,
             '🎄 Navidad se viene — ¿ya tenés los vuelos para las fiestas?', TRUE)
    """))

    # -------------------------------------------------------------------------
    # Seed: fechas comerciales 2026
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        INSERT INTO special_dates
            (nombre, fecha, tipo, anticipacion_dias, mensaje_sugerido, activo)
        VALUES
            ('Hot Sale 2026',
             '2026-05-18', 'comercial', 5,
             '🔥 Hot Sale: los mejores precios del año en vuelos. ¡No te lo pierdas!', TRUE),

            ('Día de la Madre 2026',
             '2026-10-18', 'comercial', 7,
             '💐 Día de la Madre: el mejor regalo es un viaje ✈️', TRUE),

            ('Día del Padre 2026',
             '2026-06-21', 'comercial', 7,
             '👨 Día del Padre: regalale una experiencia de viaje ✈️', TRUE),

            ('Black Friday 2026',
             '2026-11-27', 'comercial', 5,
             '⚫ Black Friday: descuentos imperdibles en vuelos. ¡Comprá ya!', TRUE),

            ('CyberMonday 2026',
             '2026-11-30', 'comercial', 5,
             '💻 CyberMonday: ¡vuelos al precio más bajo del año! No te quedes afuera.', TRUE),

            ('Fin de Año 2026',
             '2026-12-31', 'comercial', 10,
             '🎆 Fin de año: ¿dónde vas a festejar? Buscá tu vuelo antes de que se agoten.', TRUE),

            ('Temporada Verano 2027',
             '2027-01-15', 'vacaciones', 14,
             '☀️ Verano se viene — reservá tus vacaciones antes de que suban los precios!', TRUE)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_special_dates_activo"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_special_dates_fecha"))
    op.execute(sa.text("DROP TABLE IF EXISTS special_dates"))
