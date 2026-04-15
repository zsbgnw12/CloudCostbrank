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
    product: Mapped[str | None] = mapped_column(String(200))
    usage_type: Mapped[str | None] = mapped_column(String(300))
    region: Mapped[str | None] = mapped_column(String(50))
    cost: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), nullable=False)
    usage_quantity: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), default=Decimal("0"))
    usage_unit: Mapped[str | None] = mapped_column(String(50))
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    tags: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    additional_info: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    # relationships
    data_source = relationship("DataSource", back_populates="billing_data")
