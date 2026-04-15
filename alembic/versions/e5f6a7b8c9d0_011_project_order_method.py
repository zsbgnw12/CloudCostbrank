"""011 projects.order_method 下单方式"""

from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("order_method", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "order_method")
