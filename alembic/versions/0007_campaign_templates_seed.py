"""campaign templates seed

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-24

Agrega templates de notificación para el nivel 'campaign' por país.
Variables disponibles: {origin}, {destination}, {origin_iata}, {destination_iata}
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Templates por país + drop_level='campaign'
_TEMPLATES = [
    # Argentina
    ("AR", "✈️ {origin} → {destination} está re copado ahora", "Hay vuelos piolas para {destination}. ¡Fiera, dale un vistazo!"),
    ("AR", "🔥 Fijate esta ruta: {origin_iata}→{destination_iata}", "Vuelos a {destination} con precios que se ven bien. ¡No aflojes!"),
    ("AR", "📍 {destination} te espera, che", "Aprovechá que hay vuelos desde {origin} a buen precio. ¡Golazo!"),
    ("AR", "🗺️ ¿Y si volás a {destination}?", "Hay opciones desde {origin_iata} que están joya. Chequealo!"),
    # México
    ("MX", "✈️ {origin} → {destination} ¡órale!", "Hay vuelos a {destination} que están de pelos. ¡Dale gas!"),
    ("MX", "🔥 No te rajes: {origin_iata}→{destination_iata}", "Vuelos a {destination} con precios chidos. ¡Échale un ojo!"),
    ("MX", "📍 ¿Ya viste los vuelos a {destination}?", "Desde {origin} hay opciones que están cañón. ¡Aguas, se van!"),
    # Colombia
    ("CO", "✈️ {origin} → {destination} ¡qué nota!", "Hay tiquetes a {destination} bacanos. ¡Parce, de una!"),
    ("CO", "🔥 Pilas: vuelos {origin_iata}→{destination_iata}", "Tiquetes a {destination} con precios chimba. ¡No hay tos!"),
    # Chile
    ("CL", "✈️ {origin} → {destination} ¡la raja po!", "Hay vuelos a {destination} que están bacán. ¡Al tiro!"),
    ("CL", "🔥 Cachai: vuelos {origin_iata}→{destination_iata}", "Opciones a {destination} que están filete. ¡Al tiro!"),
    # Brasil (portugués)
    ("BR", "✈️ {origin} → {destination} partiu!", "Tem voos para {destination} que estão topzera. Bora aproveitar!"),
    ("BR", "🔥 Cara, olha essa rota: {origin_iata}→{destination_iata}", "Voos para {destination} com preços que são show de bola!"),
    # Genérico
    ("*", "✈️ {origin} → {destination} ¡gran oportunidad!", "Hay vuelos a {destination} con buenos precios. ¡Aprovechá!"),
    ("*", "🔥 Ruta destacada: {origin_iata} → {destination_iata}", "Vuelos a {destination} disponibles ahora. ¡No te lo pierdas!"),
    ("*", "📍 ¿Pensaste volar a {destination}?", "Hay opciones desde {origin} con precios interesantes."),
]


def upgrade() -> None:
    for country_code, title, body in _TEMPLATES:
        op.execute(sa.text("""
            INSERT INTO notification_templates
                (country_code, drop_level, title_template, body_template, is_active, created_at)
            VALUES
                (:cc, 'campaign', :title, :body, TRUE, NOW())
        """).bindparams(cc=country_code, title=title, body=body))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM notification_templates WHERE drop_level = 'campaign'
    """))
