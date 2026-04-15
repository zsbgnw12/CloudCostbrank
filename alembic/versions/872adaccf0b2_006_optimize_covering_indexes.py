"""006_optimize_covering_indexes

Replace simple indexes with covering indexes that include the cost column,
so aggregation queries (SUM(cost)) can be answered from the index alone
without heap fetches.  Also drop the standalone ix_billing_date index
(subsumed by composite indexes).

Revision ID: 872adaccf0b2
Revises: 005
Create Date: 2026-04-09 16:10:29.836584
"""
from typing import Sequence, Union

from alembic import op

revision: str = '872adaccf0b2'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_billing_date")
    op.execute("DROP INDEX IF EXISTS ix_billing_provider_date")
    op.execute("DROP INDEX IF EXISTS ix_billing_project_date")
    op.execute("DROP INDEX IF EXISTS ix_billing_ds_id")
    op.execute("DROP INDEX IF EXISTS ix_billing_provider")

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_billing_provider_date_cost
        ON billing_data (provider, date, cost)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_billing_project_date_cost
        ON billing_data (project_id, date, cost)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_billing_provider_date_cost")
    op.execute("DROP INDEX IF EXISTS ix_billing_project_date_cost")

    op.execute("CREATE INDEX IF NOT EXISTS ix_billing_date ON billing_data (date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_billing_provider_date ON billing_data (provider, date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_billing_project_date ON billing_data (project_id, date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_billing_provider ON billing_data (provider)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_billing_ds_id ON billing_data (data_source_id)")
