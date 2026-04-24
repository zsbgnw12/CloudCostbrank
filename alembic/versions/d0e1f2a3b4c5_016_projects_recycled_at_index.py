"""016 index on projects.recycled_at for soft-delete filter queries.

`recycled_at` column itself already exists in the live schema; this migration
just adds the supporting index. Run order:

    alembic upgrade head
"""

from alembic import op
import sqlalchemy as sa


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 生产 DB 已经有 recycled_at 列（历史 schema 保留），模型层本次才用起来。
    # 只加索引支持 list_accounts / get_account 的 `WHERE recycled_at IS NULL` 快速过滤。
    op.create_index(
        "ix_projects_recycled_at",
        "projects",
        ["recycled_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_projects_recycled_at", table_name="projects", if_exists=True)
