from datetime import datetime

from sqlalchemy import String, Text, ForeignKey, UniqueConstraint, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProjectCustomerAssignment(Base):
    """销售系统下发的客户编号 ↔ 服务账号（Project）关联。N:M。"""

    __tablename__ = "project_customer_assignments"
    __table_args__ = (
        UniqueConstraint("project_id", "customer_code", name="uq_pca_project_customer"),
        Index("ix_pca_customer_code", "customer_code"),
        Index("ix_pca_project_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # 上游销售系统的客户编号；不做本地 FK，已做 upper()+strip() 归一化。
    customer_code: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_by: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())

    project = relationship("Project", back_populates="customer_assignments")
