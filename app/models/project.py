from datetime import datetime

from sqlalchemy import String, Text, ForeignKey, UniqueConstraint, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Project(Base):
    """服务账号 / 云项目。云厂商类型只来自 supply_sources.provider，不在此表重复存储。"""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("supply_source_id", "external_project_id", name="uq_project_supply_src_ext_id"),
        Index("ix_projects_status", "status"),
        Index("ix_projects_supply_source_id", "supply_source_id"),
        Index("ix_projects_recycled_at", "recycled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_project_id: Mapped[str] = mapped_column(String(200), nullable=False)
    supply_source_id: Mapped[int] = mapped_column(ForeignKey("supply_sources.id"), nullable=False)
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    status: Mapped[str] = mapped_column(String(15), default="active")  # active / inactive / standby
    notes: Mapped[str | None] = mapped_column(Text)
    order_method: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 下单方式：MCCL-EA / HK CSP 等
    # 软删除标记。非 NULL 即视为已删除，所有面向用户的查询都应过滤 recycled_at IS NULL。
    # 物理删除改为打时间戳后，账单数据、sync 历史全部保留不动，前端从服务账号列表里消失。
    recycled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    supply_source = relationship("SupplySource", back_populates="projects")
    data_source = relationship("DataSource", back_populates="projects")
    category = relationship("Category", back_populates="projects")
    assignment_logs = relationship("ProjectAssignmentLog", back_populates="project", order_by="ProjectAssignmentLog.created_at")
    customer_assignments = relationship(
        "ProjectCustomerAssignment",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectCustomerAssignment.assigned_at",
    )
