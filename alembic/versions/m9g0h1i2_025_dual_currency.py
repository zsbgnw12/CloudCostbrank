"""025 双币金额:billing_summary / billing_daily_summary 增加 cost_usd / cost_cny

背景:
- 各云原始 `cost` + `currency` 保持不变(原始账单币种,审计/发票对账唯一真相)。
- 新增 `cost_usd`(采集时固化的美元规范化金额)—— 所有内部聚合的唯一口径,永不混币相加。
- 新增 `cost_cny`(采集时固化的人民币金额)—— 给中国客户出账/展示的冻结额。

换算规则(入库时固化一次,历史不随汇率变动):
- USD-native(aws/gcp/taiji、USD 结算的 azure):cost_usd = cost;cost_cny = cost × 当日 USD→CNY。
- CNY 结算的 azure:cost_cny = cost;cost_usd = cost / currency_conversion_rate
  (Azure 的 ExchangeRatePricingToBilling = pricing(USD)→billing(CNY),故除之得 USD)。

分区表:billing_summary 是 PARTITION BY RANGE(date),父表 add_column 会自动下放到所有月分区。

Revision ID: m9g0h1i2
Revises: l8f9g0h1
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa


revision = "m9g0h1i2"
down_revision = "l8f9g0h1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # billing_summary(分区父表,列自动下放到各分区)
    op.add_column("billing_summary", sa.Column("cost_usd", sa.DECIMAL(20, 6), nullable=True))
    op.add_column("billing_summary", sa.Column("cost_cny", sa.DECIMAL(20, 6), nullable=True))

    # billing_daily_summary 预聚合表
    op.add_column("billing_daily_summary", sa.Column("total_cost_usd", sa.DECIMAL(20, 6), nullable=True))
    op.add_column("billing_daily_summary", sa.Column("total_cost_cny", sa.DECIMAL(20, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("billing_daily_summary", "total_cost_cny")
    op.drop_column("billing_daily_summary", "total_cost_usd")
    op.drop_column("billing_summary", "cost_cny")
    op.drop_column("billing_summary", "cost_usd")
