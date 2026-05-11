from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Entity(Base):
    """主体：供应商在某朵云下挂的法人/下单主体，介于 SupplySource 与 Project 之间。"""

    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("supply_source_id", "name", name="uq_entity_supply_src_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    supply_source_id: Mapped[int] = mapped_column(
        ForeignKey("supply_sources.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    supply_source = relationship("SupplySource", back_populates="entities")
    projects = relationship("Project", back_populates="entity")
