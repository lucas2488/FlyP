"""baseline — estado inicial de la DB (todas las tablas ya existen en prod)

Revision ID: 0001
Revises: —
Create Date: 2026-05-24

Esta migración representa el estado de la base de datos al momento de
activar Alembic. Las tablas ya existen en producción; esta revisión
actúa como punto de partida sin ejecutar DDL.

Para marcar un servidor existente como "ya en este estado" sin correr DDL:
    alembic stamp 0001
"""
from typing import Sequence, Union

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Las tablas ya existen — esta migración es solo un marcador de estado.
    # Para instalaciones nuevas, SQLAlchemy create_all() en el lifespan
    # se encarga de crear todas las tablas antes de que Alembic corra.
    pass


def downgrade() -> None:
    pass
