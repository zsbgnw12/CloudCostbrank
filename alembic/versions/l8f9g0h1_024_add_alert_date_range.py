"""024 为 alert_rules 添加自定义时间段字段

支持自定义时间段的多项目预算合计告警，允许查询历史年份数据。
新增字段：
- start_date: 开始日期（可选）
- end_date: 结束日期（可选）

用于新的告警类型 custom_period_budget_multi，可以查询任意时间段的费用合计。

Revision ID: l8f9g0h1
Revises: k7e8f9g0
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa


revision = "l8f9g0h1"
down_revision = "k7e8f9g0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加自定义时间段字段
    op.add_column("alert_rules", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("alert_rules", sa.Column("end_date", sa.Date(), nullable=True))


def downgrade() -> None:
    # 删除自定义时间段字段
    op.drop_column("alert_rules", "end_date")
    op.drop_column("alert_rules", "start_date")
