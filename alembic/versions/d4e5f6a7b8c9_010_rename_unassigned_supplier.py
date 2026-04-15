"""010 将历史默认供应商名统一为「未分配资源组」

兼容「其他货源」「未分配货源组」；若已存在「未分配资源组」则不再改名（避免重复）。"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

TARGET = "未分配资源组"
LEGACY = ("其他货源", "未分配货源组")


def upgrade() -> None:
    conn = op.get_bind()
    for old in LEGACY:
        conn.execute(
            sa.text("""
                UPDATE suppliers SET name = :target
                WHERE name = :old
                AND NOT EXISTS (SELECT 1 FROM suppliers WHERE name = :target)
            """),
            {"target": TARGET, "old": old},
        )


def downgrade() -> None:
    pass
