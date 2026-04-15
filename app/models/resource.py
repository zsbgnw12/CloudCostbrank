from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Integer, DECIMAL, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ResourceInventory(Base):
    __tablename__ = "resource_inventory"
    __table_args__ = (
        UniqueConstraint("provider", "resource_id", name="uq_resource_provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(200))
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    resource_id: Mapped[str | None] = mapped_column(String(500))
    resource_name: Mapped[str | None] = mapped_column(String(200))
    resource_type: Mapped[str | None] = mapped_column(String(100))
    product: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="active")
    tags: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    monthly_cost: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))
    first_seen_at: Mapped[datetime | None] = mapped_column()
    last_seen_at: Mapped[datetime | None] = mapped_column()

    # relationships
    data_source = relationship("DataSource", back_populates="resources")
