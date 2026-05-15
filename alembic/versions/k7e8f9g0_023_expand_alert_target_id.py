"""023 扩展 alert_rules.target_id 字段长度以支持多项目告警

当告警规则的 target_type 为 project_group 时，target_id 存储逗号分隔的项目 UUID 列表。
原 VARCHAR(200) 限制导致多项目告警创建失败，现扩展为 TEXT 类型。

Revision ID: k7e8f9g0
Revises: j6d7e8f9
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa


revision = "k7e8f9g0"
down_revision = "j6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 扩展 alert_rules.target_id 从 VARCHAR(200) 到 TEXT
    op.alter_column(
        "alert_rules",
        "target_id",
        type_=sa.Text(),
        existing_type=sa.String(200),
        existing_nullable=True
    )


def downgrade() -> None:
    # 回滚到 VARCHAR(200)
    # 警告：如果已有数据超过 200 字符，回滚会失败
    op.alter_column(
        "alert_rules",
        "target_id",
        type_=sa.String(200),
        existing_type=sa.Text(),
        existing_nullable=True
    )
