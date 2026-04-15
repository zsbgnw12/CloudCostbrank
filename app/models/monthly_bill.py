from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Text, DECIMAL, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MonthlyBill(Base):
    __tablename__ = "monthly_bills"
    __table_args__ = (
        UniqueConstraint("month", "category_id", "provider", name="uq_monthly_bill"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(10))  # NULL = all providers
    original_cost: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    markup_rate: Mapped[Decimal] = mapped_column(DECIMAL(5, 4), nullable=False)
    final_cost: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    adjustment: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(15), default="draft")  # draft/confirmed/sent/paid
    confirmed_at: Mapped[datetime | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # relationships
    category = relationship("Category", back_populates="monthly_bills")
