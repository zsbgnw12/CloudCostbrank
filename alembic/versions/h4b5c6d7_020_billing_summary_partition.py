"""020 把 billing_data rename 为分区表 billing_summary

按 v3-final §4 规格：
1. 前置检查 cost_type 无 NULL（019 之后应已为 0）
2. 建分区父表 billing_summary，PK = (id, date)，PARTITION BY RANGE (date)
   字段 = billing_data 当前所有列 + 新增 etl_run_id BIGINT NULL（D15 预留）
3. 建 default 分区 billing_summary_default
4. 按历史 MIN/MAX(date) 循环建月分区，外加未来 3 个月
5. 在父表 declare 索引/唯一约束（自动下放到子分区）
6. INSERT INTO billing_summary SELECT ... FROM billing_data（etl_run_id 写 NULL）
7. ALTER TABLE billing_data RENAME TO _billing_data_legacy（不 DROP，30 天观察期）
8. CREATE VIEW billing_data 指向 billing_summary（显式列、不暴露 etl_run_id）
9. setval('billing_summary_id_seq', MAX(id)) — 防新插入 id 冲突
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "h4b5c6d7"
down_revision = "g3a4b5c6d7e8"
branch_labels = None
depends_on = None


# ── billing_data 当前列（含 016-019 增量）；id/date 是分区键和 PK 一部分 ─────
# 顺序与 BillingData ORM 模型一致；新增 etl_run_id（D15）
_BILLING_COLUMNS_DDL = """
    id BIGSERIAL NOT NULL,
    date DATE NOT NULL,
    provider VARCHAR(10) NOT NULL,
    data_source_id INTEGER NOT NULL REFERENCES data_sources(id),
    project_id VARCHAR(200),
    project_name VARCHAR(200),
    service_id VARCHAR(40),
    sku_id VARCHAR(40),
    product VARCHAR(200),
    usage_type VARCHAR(300),
    region VARCHAR(50),
    cost NUMERIC(20, 6) NOT NULL,
    cost_at_list NUMERIC(20, 6),
    credits_total NUMERIC(20, 6),
    credits_breakdown JSONB,
    usage_quantity NUMERIC(20, 6) DEFAULT 0,
    usage_unit VARCHAR(50),
    currency VARCHAR(10) DEFAULT 'USD',
    currency_conversion_rate NUMERIC(20, 10),
    resource_name VARCHAR(500),
    cost_type VARCHAR(20) NOT NULL DEFAULT 'regular',
    billing_account_id VARCHAR(40),
    invoice_month VARCHAR(7),
    transaction_type VARCHAR(40),
    seller_name VARCHAR(200),
    consumption_model_id VARCHAR(40),
    consumption_model_description VARCHAR(200),
    tags JSONB DEFAULT '{}'::jsonb,
    system_labels JSONB,
    additional_info JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    etl_run_id BIGINT NULL,
    PRIMARY KEY (id, date)
"""


# 用于 INSERT 阶段的列列表（不含 etl_run_id —— 老表没有这一列）
_COPY_COLUMNS = [
    "id", "date", "provider", "data_source_id", "project_id", "project_name",
    "service_id", "sku_id", "product", "usage_type", "region",
    "cost", "cost_at_list", "credits_total", "credits_breakdown",
    "usage_quantity", "usage_unit", "currency", "currency_conversion_rate",
    "resource_name", "cost_type", "billing_account_id", "invoice_month",
    "transaction_type", "seller_name", "consumption_model_id",
    "consumption_model_description", "tags", "system_labels",
    "additional_info", "created_at",
]


# VIEW 列列表 —— 显式去掉 etl_run_id，避免老调用方意外看见新列
_VIEW_COLUMNS = list(_COPY_COLUMNS)


def _month_floor(d):
    """date → 该月 1 号"""
    import datetime as dt
    return dt.date(d.year, d.month, 1)


def _next_month(d):
    """月 1 号 → 下个月 1 号"""
    import datetime as dt
    if d.month == 12:
        return dt.date(d.year + 1, 1, 1)
    return dt.date(d.year, d.month + 1, 1)


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. 前置检查 cost_type 无 NULL ─────────────────────────────────
    null_cost_type = bind.execute(
        text("SELECT COUNT(*) FROM billing_data WHERE cost_type IS NULL")
    ).scalar()
    if null_cost_type and null_cost_type > 0:
        raise RuntimeError(
            f"billing_data 有 {null_cost_type} 行 cost_type IS NULL；"
            "请先 UPDATE billing_data SET cost_type='regular' WHERE cost_type IS NULL，"
            "再跑 020。"
        )

    # ── 2. 建分区父表 billing_summary ─────────────────────────────────
    op.execute(text(f"""
        CREATE TABLE billing_summary (
            {_BILLING_COLUMNS_DDL}
        ) PARTITION BY RANGE (date)
    """))

    # ── 3. 建 default 分区 ────────────────────────────────────────────
    op.execute(text("""
        CREATE TABLE billing_summary_default
        PARTITION OF billing_summary DEFAULT
    """))

    # ── 4. 按历史月范围 + 未来 3 个月建子分区 ────────────────────────
    row = bind.execute(text(
        "SELECT date_trunc('month', MIN(date))::date, "
        "date_trunc('month', MAX(date))::date FROM billing_data"
    )).first()

    import datetime as dt
    if row and row[0] is not None:
        start_month = row[0]
        end_month = row[1]
    else:
        # 空表场景：从当月开始
        today = dt.date.today()
        start_month = dt.date(today.year, today.month, 1)
        end_month = start_month

    # 未来 3 个月兜底
    future_end = end_month
    for _ in range(3):
        future_end = _next_month(future_end)

    cur = start_month
    while cur <= future_end:
        nxt = _next_month(cur)
        part_name = f"billing_summary_{cur.year:04d}{cur.month:02d}"
        op.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {part_name}
            PARTITION OF billing_summary
            FOR VALUES FROM ('{cur.isoformat()}') TO ('{nxt.isoformat()}')
        """))
        cur = nxt

    # ── 5a. 释放旧 billing_data 上的约束/索引名（PG 中约束与索引名是 schema 全局唯一）
    # 旧表稍后会 RENAME 成 _billing_data_legacy，但约束/索引名不会跟随；
    # 必须显式改名让新 billing_summary 能复用同名。
    op.execute(text("ALTER TABLE billing_data RENAME CONSTRAINT uix_billing_dedup TO uix_billing_data_legacy_dedup"))
    op.execute(text("ALTER INDEX ix_billing_ds_date RENAME TO ix_billing_data_legacy_ds_date"))
    op.execute(text("ALTER INDEX ix_billing_project_date_cost RENAME TO ix_billing_data_legacy_project_date_cost"))
    op.execute(text("ALTER INDEX ix_billing_provider_date_cost RENAME TO ix_billing_data_legacy_provider_date_cost"))
    op.execute(text("ALTER INDEX ix_billing_invoice_month RENAME TO ix_billing_data_legacy_invoice_month"))
    op.execute(text("ALTER INDEX ix_billing_account_id_date RENAME TO ix_billing_data_legacy_account_id_date"))
    op.execute(text("ALTER INDEX ix_billing_sku_id_date RENAME TO ix_billing_data_legacy_sku_id_date"))

    # ── 5. 在父表 declare 索引 / 唯一约束（自动下放到子分区） ──────
    # 唯一约束（含 cost_type，含分区键 date）
    op.execute(text("""
        ALTER TABLE billing_summary
        ADD CONSTRAINT uix_billing_dedup UNIQUE
        (date, data_source_id, project_id, product, usage_type, region, cost_type)
    """))

    # 6 个索引
    op.execute(text("CREATE INDEX ix_billing_ds_date ON billing_summary (data_source_id, date)"))
    op.execute(text("CREATE INDEX ix_billing_project_date_cost ON billing_summary (project_id, date, cost)"))
    op.execute(text("CREATE INDEX ix_billing_provider_date_cost ON billing_summary (provider, date, cost)"))
    op.execute(text("CREATE INDEX ix_billing_invoice_month ON billing_summary (invoice_month)"))
    op.execute(text("CREATE INDEX ix_billing_account_id_date ON billing_summary (billing_account_id, date)"))
    op.execute(text("CREATE INDEX ix_billing_sku_id_date ON billing_summary (sku_id, date)"))

    # ── 6. 搬数据 ────────────────────────────────────────────────────
    cols_str = ", ".join(_COPY_COLUMNS)
    op.execute(text(f"""
        INSERT INTO billing_summary ({cols_str}, etl_run_id)
        SELECT {cols_str}, NULL FROM billing_data
    """))

    # ── 7. 重命名旧表（不 DROP，30 天后人工清理）──────────────────
    op.execute(text("ALTER TABLE billing_data RENAME TO _billing_data_legacy"))

    # ── 8. 建只读 VIEW billing_data（显式列，不含 etl_run_id）──────
    view_cols = ", ".join(_VIEW_COLUMNS)
    op.execute(text(f"CREATE VIEW billing_data AS SELECT {view_cols} FROM billing_summary"))

    # ── 9. 序列同步 ──────────────────────────────────────────────────
    op.execute(text("""
        SELECT setval(
            'billing_summary_id_seq',
            COALESCE((SELECT MAX(id) FROM billing_summary), 1),
            (SELECT MAX(id) IS NOT NULL FROM billing_summary)
        )
    """))


def downgrade() -> None:
    bind = op.get_bind()

    # 检查 _billing_data_legacy 是否还在；若已被人工 DROP 则报错
    legacy_exists = bind.execute(text(
        "SELECT to_regclass('public._billing_data_legacy') IS NOT NULL"
    )).scalar()
    if not legacy_exists:
        raise RuntimeError(
            "_billing_data_legacy 已被人工 DROP（30 天观察期后清理），"
            "无法直接 downgrade；请从 PITR 恢复 billing_data 表。"
        )

    op.execute(text("DROP VIEW IF EXISTS billing_data"))
    op.execute(text("ALTER TABLE _billing_data_legacy RENAME TO billing_data"))
    op.execute(text("DROP TABLE IF EXISTS billing_summary CASCADE"))
