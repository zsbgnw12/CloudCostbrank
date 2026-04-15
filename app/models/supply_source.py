from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SupplySource(Base):
    """货源：某供应商在某朵云下的一条线；云类型 provider 只存此处（与 Project 不再重复）。"""

    __tablename__ = "supply_sources"
    __table_args__ = (UniqueConstraint("supplier_id", "provider", name="uq_supply_src_supplier_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)  # aws / gcp / azure
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    supplier = relationship("Supplier", back_populates="supply_sources")
    projects = relationship("Project", back_populates="supply_source")
