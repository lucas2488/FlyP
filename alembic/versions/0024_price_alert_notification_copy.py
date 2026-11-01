"""
0024 — Texto de los avisos de precio

price_alert_notifications: título y texto del push (escritos por Claude o la plantilla)
y de dónde salieron (copy_source: llm | template).
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_alert_notifications", sa.Column("title", sa.Text))
    op.add_column("price_alert_notifications", sa.Column("body", sa.Text))
    op.add_column("price_alert_notifications", sa.Column("copy_source", sa.String(16)))


def downgrade() -> None:
    op.drop_column("price_alert_notifications", "copy_source")
    op.drop_column("price_alert_notifications", "body")
    op.drop_column("price_alert_notifications", "title")
