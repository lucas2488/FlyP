"""
0020 — Tabla impact_links (cache de tracking links de Impact.com)

Cachea el tracking link de Impact por deeplink para desacoplar creación de uso:
se pre-crean al abrir el detalle (prewarm) y se reusan en el click (resolve).
Dedup por hash SHA-256 del deeplink (la url es demasiado larga para un índice
único btree directo, límite ~2700 bytes).
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "impact_links",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("impact_url", sa.Text, nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_impact_links_url_hash", "impact_links", ["url_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_impact_links_url_hash", table_name="impact_links")
    op.drop_table("impact_links")
