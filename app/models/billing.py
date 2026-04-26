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
        UniqueConstraint(
            "date", "data_source_id", "project_id", "product", "usage_type", "region",
            name="uix_billing_dedup",
        ),
        Index("ix_billing_ds_date", "data_source_id", "date"),
        Index("ix_billing_project_date_cost", "project_id", "date", "cost"),
        Index("ix_billing_provider_date_cost", "provider", "date", "cost"),
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
    # 节省金额（CUD/SUD/promo/free_tier 折扣总和，正数表示节省了多少钱）。
    # GCP credits 数组的 -SUM(amount)。其他 provider 写 NULL 或 0。
    credits_total: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 6))
    usage_quantity: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), default=Decimal("0"))
    usage_unit: Mapped[str | None] = mapped_column(String(50))
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    # 资源 ID（如 "//compute.googleapis.com/projects/.../instances/123"）。
    # 一个 SKU-day 通常聚合了多个资源，这里取 cost 最高的那个作代表。
    resource_name: Mapped[str | None] = mapped_column(String(500))
    # cost_type: regular / tax / adjustment / rounding_error。聚合时 ANY_VALUE，
    # 不参与去重；同一组 dedup key 多种 cost_type 共存时只代表一种（少数派被吞）。
    # 完整的 tax/adjustment 拆分查 BQ。
    cost_type: Mapped[str | None] = mapped_column(String(20))
    tags: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    additional_info: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    # relationships
    data_source = relationship("DataSource", back_populates="billing_data")
