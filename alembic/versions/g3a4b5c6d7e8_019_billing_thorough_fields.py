"""019 彻底修：按 BQ 真实 schema 对齐 billing_data 字段，修 cost_type 吞 tax 的去重 bug

改动说明：

1. 删 credits_committed / credits_other / total_credits_committed / total_credits_other
   原因：对当前 reseller 数据这两个字段永远是 0/null（type 实际是 RESELLER_MARGIN/PROMOTION/DISCOUNT，
   没有 COMMITTED_USAGE_DISCOUNT），保留只会误导。改用 credits_breakdown JSONB 原样保留分类信息。

2. 加 9 个新字段（按 BQ 真实 schema）：
   - billing_account_id           BQ 顶层字段，计费账号（"01186D-EC0E18-F83B2B"）
   - invoice_month                invoice.month，发票月（"202603"）
   - transaction_type             "THIRD_PARTY_RESELLER" / "REGULAR" / 等
   - seller_name                  转售方（"Anthropic" / "Google"）
   - currency_conversion_rate     汇率，USD 是 1.0
   - consumption_model_id         consumption_model.id（'7754-699E-0EBF' Default 等）
   - consumption_model_description consumption_model.description
   - system_labels                JSONB，GCP 自动打的系统标签
   - credits_breakdown            JSONB，原样保留 credits 数组按 type 拆分的金额

3. 修 cost_type tax 吞 bug：
   - 把 cost_type 加进 unique 约束（区分 regular / tax / adjustment 不再混算）
   - 旧数据 cost_type NULL 默认填 'regular'，再设 NOT NULL DEFAULT 'regular'
   - 新约束: (date, ds, project_id, product, usage_type, region, cost_type)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "g3a4b5c6d7e8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. 删过时列 ──────────────────────────────────────────
    op.drop_column("billing_data", "credits_committed")
    op.drop_column("billing_data", "credits_other")
    op.drop_column("billing_daily_summary", "total_credits_committed")
    op.drop_column("billing_daily_summary", "total_credits_other")

    # ── 2. 加新字段 ──────────────────────────────────────────
    op.add_column("billing_data", sa.Column("billing_account_id", sa.String(40), nullable=True))
    op.add_column("billing_data", sa.Column("invoice_month", sa.String(7), nullable=True))
    op.add_column("billing_data", sa.Column("transaction_type", sa.String(40), nullable=True))
    op.add_column("billing_data", sa.Column("seller_name", sa.String(200), nullable=True))
    op.add_column("billing_data", sa.Column("currency_conversion_rate", sa.Numeric(20, 10), nullable=True))
    op.add_column("billing_data", sa.Column("consumption_model_id", sa.String(40), nullable=True))
    op.add_column("billing_data", sa.Column("consumption_model_description", sa.String(200), nullable=True))
    op.add_column("billing_data", sa.Column("system_labels", JSONB, nullable=True))
    op.add_column("billing_data", sa.Column("credits_breakdown", JSONB, nullable=True))

    # 索引（高频按 invoice_month / billing_account 查询）
    op.create_index("ix_billing_invoice_month", "billing_data", ["invoice_month"], if_not_exists=True)
    op.create_index("ix_billing_account_id_date", "billing_data", ["billing_account_id", "date"], if_not_exists=True)

    # ── 3. cost_type 加进 unique 约束 ──────────────────────
    # 现存 NULL cost_type 默认 'regular'（绝大多数都是 regular）
    op.execute("UPDATE billing_data SET cost_type = 'regular' WHERE cost_type IS NULL")
    op.alter_column("billing_data", "cost_type",
                    existing_type=sa.String(20),
                    server_default="regular",
                    nullable=False)
    # 删旧约束、加新约束（包含 cost_type）
    op.drop_constraint("uix_billing_dedup", "billing_data", type_="unique")
    op.create_unique_constraint(
        "uix_billing_dedup",
        "billing_data",
        ["date", "data_source_id", "project_id", "product", "usage_type", "region", "cost_type"],
    )


def downgrade() -> None:
    op.drop_constraint("uix_billing_dedup", "billing_data", type_="unique")
    op.create_unique_constraint(
        "uix_billing_dedup",
        "billing_data",
        ["date", "data_source_id", "project_id", "product", "usage_type", "region"],
    )
    op.alter_column("billing_data", "cost_type",
                    existing_type=sa.String(20),
                    server_default=None,
                    nullable=True)

    op.drop_index("ix_billing_account_id_date", table_name="billing_data", if_exists=True)
    op.drop_index("ix_billing_invoice_month", table_name="billing_data", if_exists=True)

    op.drop_column("billing_data", "credits_breakdown")
    op.drop_column("billing_data", "system_labels")
    op.drop_column("billing_data", "consumption_model_description")
    op.drop_column("billing_data", "consumption_model_id")
    op.drop_column("billing_data", "currency_conversion_rate")
    op.drop_column("billing_data", "seller_name")
    op.drop_column("billing_data", "transaction_type")
    op.drop_column("billing_data", "invoice_month")
    op.drop_column("billing_data", "billing_account_id")

    op.add_column("billing_daily_summary", sa.Column("total_credits_other", sa.Numeric(20, 6), nullable=True))
    op.add_column("billing_daily_summary", sa.Column("total_credits_committed", sa.Numeric(20, 6), nullable=True))
    op.add_column("billing_data", sa.Column("credits_other", sa.Numeric(20, 6), nullable=True))
    op.add_column("billing_data", sa.Column("credits_committed", sa.Numeric(20, 6), nullable=True))
