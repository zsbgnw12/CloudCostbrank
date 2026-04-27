"""018 拆分 credits_total 成 节省计划 + 其他节省

billing_data:
  - credits_committed   GCP credits 数组里 type='COMMITTED_USAGE_DISCOUNT'（节省计划/CUD）
  - credits_other       其他类型（SUSTAINED_USAGE_DISCOUNT / PROMOTION / FREE_TIER）

billing_daily_summary:
  - total_credits_committed
  - total_credits_other

credits_total 保留 = committed + other 用于一致性校验。
新字段都允许 NULL；老数据 NULL；新 sync 进来按 BQ credits.type 自动拆分。
"""

from alembic import op
import sqlalchemy as sa


revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("billing_data", sa.Column("credits_committed", sa.DECIMAL(20, 6), nullable=True))
    op.add_column("billing_data", sa.Column("credits_other", sa.DECIMAL(20, 6), nullable=True))
    op.add_column("billing_daily_summary", sa.Column("total_credits_committed", sa.DECIMAL(20, 6), nullable=True))
    op.add_column("billing_daily_summary", sa.Column("total_credits_other", sa.DECIMAL(20, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("billing_daily_summary", "total_credits_other")
    op.drop_column("billing_daily_summary", "total_credits_committed")
    op.drop_column("billing_data", "credits_other")
    op.drop_column("billing_data", "credits_committed")
