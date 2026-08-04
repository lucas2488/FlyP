"""
0019 — Ensanchar columnas de país (varchar(10) → varchar(100))

Las columnas de país guardan NOMBRES completos ("Argentina", "Estados Unidos",
"República Dominicana"), pero estaban declaradas como varchar(10). Cualquier
POST /profile con un país de nombre >10 caracteres reventaba con
StringDataRightTruncationError → 500 → el usuario no se registraba, no recibía
welcome y no se le creaba el price_watch.

Se amplían a varchar(100). Aumentar el largo de un varchar en Postgres es un
cambio de catálogo (no reescribe la tabla), así que es rápido y seguro.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels = None
depends_on = None


_COLUMNS = [
    ("user_profiles", "last_search_origin_country"),
    ("user_profiles", "last_search_destination_country"),
    ("airport_cache", "country"),
]


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table, column,
            type_=sa.String(100),
            existing_type=sa.String(10),
            existing_nullable=True,
        )


def downgrade() -> None:
    # No revertir a varchar(10): truncaría datos existentes >10 chars.
    # Se deja en varchar(100); downgrade es no-op intencional.
    pass
