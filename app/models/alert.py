from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Integer, Text, DECIMAL, Boolean, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)  # project/category/provider
    target_id: Mapped[str | None] = mapped_column(String(200))  # NULL = global
    threshold_type: Mapped[str] = mapped_column(String(30), nullable=False)  # daily_absolute/daily_increase_pct/monthly_budget/monthly_minimum_commitment
    threshold_value: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    notify_webhook: Mapped[str | None] = mapped_column(Text)
    notify_email: Mapped[str | None] = mapped_column(String(500))  # comma-separated emails
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # relationships
    history = relationship("AlertHistory", back_populates="rule")


class AlertHistory(Base):
    __tablename__ = "alert_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(nullable=False)
    actual_value: Mapped[Decimal | None] = mapped_column(DECIMAL(14, 2))
    threshold_value: Mapped[Decimal | None] = mapped_column(DECIMAL(14, 2))
    message: Mapped[str | None] = mapped_column(Text)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)

    # relationships
    rule = relationship("AlertRule", back_populates="history")


class Notification(Base):
    """In-app notifications for the bell icon."""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="warning")  # warning/success/info
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_history_id: Mapped[int | None] = mapped_column(ForeignKey("alert_history.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
