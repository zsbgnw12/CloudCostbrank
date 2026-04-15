from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Text, DECIMAL, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    markup_rate: Mapped[Decimal] = mapped_column(DECIMAL(5, 4), default=Decimal("1.0"))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    # relationships
    data_sources = relationship("DataSource", back_populates="category")
    projects = relationship("Project", back_populates="category")
    monthly_bills = relationship("MonthlyBill", back_populates="category")
