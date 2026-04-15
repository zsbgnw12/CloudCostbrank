"""Token usage metering model — tracks LLM token consumption per day/model."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    String,
    BigInteger,
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


class TokenUsage(Base):
    __tablename__ = "token_usage"
    __table_args__ = (
        UniqueConstraint(
            "date", "provider", "data_source_id", "model_id", "region",
            name="uix_token_usage_dedup",
        ),
        Index("ix_token_usage_date", "date"),
        Index("ix_token_usage_provider_date", "provider", "date"),
        Index("ix_token_usage_model_date", "model_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    data_source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id"), nullable=False,
    )
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(50))

    request_count: Mapped[int] = mapped_column(BigInteger, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)

    input_cost: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), default=Decimal("0"))
    output_cost: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), default=Decimal("0"))
    total_cost: Mapped[Decimal] = mapped_column(DECIMAL(20, 6), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(10), default="USD")

    additional_info: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    data_source = relationship("DataSource", back_populates="token_usage")
