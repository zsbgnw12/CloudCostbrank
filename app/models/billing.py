import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    String,
    Integer,
    Date,
    DECIMAL,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BillingData(Base):
    __tablename__ = "billing_data"
    __table_args__ = (
        # cost_type 加进唯一键，让 regular / tax / adjustment 各自独立成行，
        # 不再被 ANY_VALUE 吞掉混算。
        UniqueConstraint(
            "date", "data_source_id", "project_id", "product", "usage_type", "region", "cost_type",
            name="uix_billing_dedup",
        ),
        Index("ix_billing_ds_date", "data_source_id", "date"),
        Index("ix_billing_project_date_cost", "project_id", "date", "cost"),
        Index("ix_billing_provider_date_cost", "provider", "date", "cost"),
        Index("ix_billing_invoice_month", "invoice_month"),
        Index("ix_billing_account_id_date", "billing_account_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(200))
    project_name: Mapped[str | None] = mapped_column(String(200))
    # 服务/SKU 稳定标识符（GCP 才有；其他 provider 写 NULL）。BQ schema 里
    # service.id / sku.id 是字符串如 "C7E2-9256-F1AC"。和 product/usage_type
    # 描述对应但抗描述改名，便于历史趋势对齐。
    service_id: Mapped[str | None] = mapped_column(String(40))
    sku_id: Mapped[str | None] = mapped_column(String(40))
    product: Mapped[str | None] = mapped_column(String(200))
    usage_type: Mapped[str | None] = mapped_column(String(300))
    region: Mapped[str | None] = mapped_column(String(50))
    cost: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), nullable=False)
    # 标价（折扣前）。GCP 是 SUM(cost_at_list)。和 cost 的差值就是 credits 总折扣。
    # 其他 provider 暂未对接，写 NULL。
    cost_at_list: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 6))
    # 节省金额（credits 数组所有 type 的合计 × -1，正数表示节省了多少钱）。
    # 细分见 credits_breakdown JSONB。
    credits_total: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 6))
    # credits 按 type 的拆分明细（保留 BQ 原始信息，导出时可动态拆列）。
    # 形如 {"RESELLER_MARGIN": -3.21, "PROMOTION": -1.50, "DISCOUNT": -0.05}（金额是 BQ 原始负数）
    credits_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    usage_quantity: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), default=Decimal("0"))
    usage_unit: Mapped[str | None] = mapped_column(String(50))
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    # 汇率（USD 时为 1.0；CNY 等会带其他值）
    currency_conversion_rate: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 10))
    # 资源 ID（如 "//compute.googleapis.com/projects/.../instances/123"）。
    # 一个 SKU-day 通常聚合了多个资源，这里取 cost 最高的那个作代表。
    resource_name: Mapped[str | None] = mapped_column(String(500))
    # cost_type: regular / tax / adjustment / rounding_error
    # 加进了唯一约束，每种 cost_type 独立一行不再混算。默认 'regular'。
    cost_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="regular")
    # 计费账号（来自 BQ 顶层 billing_account_id），财务对账起点
    billing_account_id: Mapped[str | None] = mapped_column(String(40))
    # 发票月（来自 BQ invoice.month，"YYYYMM" 字符串如 "202604"）
    invoice_month: Mapped[str | None] = mapped_column(String(7))
    # 经销商类型 / 转售方
    transaction_type: Mapped[str | None] = mapped_column(String(40))
    seller_name: Mapped[str | None] = mapped_column(String(200))
    # GCP commitment 模型（Default / CUD / SUD 等）
    consumption_model_id: Mapped[str | None] = mapped_column(String(40))
    consumption_model_description: Mapped[str | None] = mapped_column(String(200))
    # GCP labels —— 用户打的资源标签（key/value）
    tags: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    # GCP system_labels —— GCP 自动打的系统标签
    system_labels: Mapped[dict | None] = mapped_column(JSONB)
    # 各 provider 自有元数据（subscription_id, charge_type 等）
    additional_info: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    # relationships
    data_source = relationship("DataSource", back_populates="billing_data")
