from datetime import datetime

from sqlalchemy import String, Integer, Boolean, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cloud_account_id: Mapped[int] = mapped_column(ForeignKey("cloud_accounts.id"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column()
    sync_status: Mapped[str] = mapped_column(String(20), default="pending")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # relationships
    cloud_account = relationship("CloudAccount", back_populates="data_sources")
    category = relationship("Category", back_populates="data_sources")
    projects = relationship("Project", back_populates="data_source")
    sync_logs = relationship("SyncLog", back_populates="data_source")
    billing_data = relationship("BillingData", back_populates="data_source")
    resources = relationship("ResourceInventory", back_populates="data_source")
    token_usage = relationship("TokenUsage", back_populates="data_source")
