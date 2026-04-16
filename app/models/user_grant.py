"""Fine-grained data-scope grants.

`cloud_admin` users skip these tables entirely (full access). All other roles
see only the resources explicitly granted below.
"""

from datetime import datetime

from sqlalchemy import String, Integer, ForeignKey, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserCloudAccountGrant(Base):
    """User ↔ CloudAccount — the primary data-scope boundary in cloud mgmt."""

    __tablename__ = "user_cloud_account_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "cloud_account_id", name="uq_user_cloud_account"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    cloud_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cloud_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope: Mapped[str] = mapped_column(String(10), default="READ", nullable=False)  # READ / WRITE

    granted_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserProjectGrant(Base):
    """Optional finer-grained grant: User ↔ Project.

    If a user has a CloudAccountGrant on the parent account, the project grant
    is redundant — keep for future tightening.
    """

    __tablename__ = "user_project_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_user_project"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope: Mapped[str] = mapped_column(String(10), default="READ", nullable=False)

    granted_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
