from datetime import datetime

from sqlalchemy import String, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProjectAssignmentLog(Base):
    """项目状态变更历史记录 —— 记录项目生命周期中每次状态变更。"""
    __tablename__ = "project_assignment_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    # created / activated / suspended
    from_status: Mapped[str | None] = mapped_column(String(15))
    to_status: Mapped[str | None] = mapped_column(String(15))
    operator: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # relationships
    project = relationship("Project", back_populates="assignment_logs")
