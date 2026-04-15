from datetime import datetime

from sqlalchemy import String, Text, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CloudAccount(Base):
    __tablename__ = "cloud_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)  # aws / gcp / azure
    secret_data: Mapped[str] = mapped_column(Text, nullable=False)  # AES-256-GCM encrypted JSON
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Azure-only: "legacy" = customer-owned app (tenant+client+secret in secret_data);
    # "multi_tenant" = our global app, customer only granted SP consent.
    auth_mode: Mapped[str] = mapped_column(String(20), default="legacy", nullable=False)
    # multi_tenant only: pending → granted → revoked
    consent_status: Mapped[str] = mapped_column(String(20), default="granted", nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    # relationships
    data_sources = relationship("DataSource", back_populates="cloud_account")
