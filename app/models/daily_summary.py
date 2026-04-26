import datetime as dt
from decimal import Decimal

from sqlalchemy import String, Integer, Date, DECIMAL, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BillingDailySummary(Base):
    __tablename__ = "billing_daily_summary"
    __table_args__ = (
        UniqueConstraint(
            "date", "provider", "data_source_id", "project_id", "product",
            name="uix_daily_summary_dedup",
        ),
        Index("ix_daily_summary_provider_date", "provider", "date"),
        Index("ix_daily_summary_project_date", "project_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    data_source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(200))
    product: Mapped[str | None] = mapped_column(String(200))
    total_cost: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), nullable=False, default=Decimal("0"))
    # 标价合计与折扣合计；refresh_daily_summary 直接 SUM 自 billing_data。
    # 老数据这两列是 NULL；新 sync 之后会被填上。dashboard 读时用 COALESCE 兜底。
    total_cost_at_list: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 6))
    total_credits: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 6))
    total_usage: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), default=Decimal("0"))
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
