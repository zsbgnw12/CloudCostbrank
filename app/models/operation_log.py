from datetime import datetime

from sqlalchemy import String, Integer, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-readable operator label (username / api-key name) kept for backwards-compat.
    operator: Mapped[str | None] = mapped_column(String(100))
    # Structured identity anchors — populated when request is authenticated.
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    casdoor_sub: Mapped[str | None] = mapped_column(String(128), index=True)
    auth_method: Mapped[str | None] = mapped_column(String(20))  # cc_jwt / casdoor_jwt / api_key
    ip: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[str | None] = mapped_column(String(50))
    before_data: Mapped[dict | None] = mapped_column(JSONB)
    after_data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
