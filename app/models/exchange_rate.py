import datetime as dt
from decimal import Decimal

from sqlalchemy import String, Date, DECIMAL, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint("date", "from_currency", "to_currency", name="uq_exchange_rate"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    from_currency: Mapped[str] = mapped_column(String(5), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(5), nullable=False)
    rate: Mapped[Decimal] = mapped_column(DECIMAL(12, 6), nullable=False)
