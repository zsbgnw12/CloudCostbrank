"""017 billing_data + daily_summary: 6 个新字段对齐 BQ schema

billing_data 加 6 列：
  - service_id          GCP service.id（C7E2-9256-... 形式），其他 provider NULL
  - sku_id              GCP sku.id（2737-2D33-D986 形式），其他 provider NULL
  - cost_at_list        标价（折扣前），GCP 直接来自 cost_at_list；其他 provider NULL
  - credits_total       节省金额合计（CUD/SUD/promo/free_tier），GCP credits 数组 SUM；其他 NULL
  - resource_name       资源 ID（聚合后取 cost 最高那行作代表）
  - cost_type           regular / tax / adjustment 标签，ANY_VALUE 取一个

billing_daily_summary 加 2 列：
  - total_cost_at_list  标价合计（dashboard 显示"折扣前总额"）
  - total_credits       折扣合计（dashboard 显示"省了多少"）

所有新字段都是 NULL 友好的（既存历史行保留 NULL；新 sync 进来的行会被填）。
唯一约束 / 主键 / 外键全部不变，老查询不受影响。
"""

from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # billing_data
    op.add_column("billing_data", sa.Column("service_id", sa.String(40), nullable=True))
    op.add_column("billing_data", sa.Column("sku_id", sa.String(40), nullable=True))
    op.add_column("billing_data", sa.Column("cost_at_list", sa.DECIMAL(20, 6), nullable=True))
    op.add_column("billing_data", sa.Column("credits_total", sa.DECIMAL(20, 6), nullable=True))
    op.add_column("billing_data", sa.Column("resource_name", sa.String(500), nullable=True))
    op.add_column("billing_data", sa.Column("cost_type", sa.String(20), nullable=True))

    # billing_daily_summary
    op.add_column("billing_daily_summary", sa.Column("total_cost_at_list", sa.DECIMAL(20, 6), nullable=True))
    op.add_column("billing_daily_summary", sa.Column("total_credits", sa.DECIMAL(20, 6), nullable=True))

    # 一些可能用到的辅助索引（按需）。先只加 sku_id 因为按 SKU 查趋势是常见场景。
    op.create_index(
        "ix_billing_sku_id_date",
        "billing_data",
        ["sku_id", "date"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_billing_sku_id_date", table_name="billing_data", if_exists=True)
    op.drop_column("billing_daily_summary", "total_credits")
    op.drop_column("billing_daily_summary", "total_cost_at_list")
    op.drop_column("billing_data", "cost_type")
    op.drop_column("billing_data", "resource_name")
    op.drop_column("billing_data", "credits_total")
    op.drop_column("billing_data", "cost_at_list")
    op.drop_column("billing_data", "sku_id")
    op.drop_column("billing_data", "service_id")
