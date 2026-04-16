"""API keys for machine-to-machine integration (AI brain, ticket system, etc).

We store only a SHA-256 hash; the plaintext is returned once at creation and
never again. Every call still resolves to the `owner_user` for scope filtering,
unless `allowed_cloud_account_ids` is explicitly set (and then intersected with
the owner's grants).
"""

from datetime import datetime

from sqlalchemy import String, Integer, ForeignKey, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # SHA-256(plaintext) hex — 64 chars. Unique so we can look up by hash quickly.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Short (first 8 chars of plaintext) prefix for UI display, e.g. "cc_ab12…"
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    # Subset of ApiModulePermission.module values; empty/null = inherit owner's role scope
    allowed_modules: Mapped[list | None] = mapped_column(JSONB)
    # Subset of cloud_account ids visible to the owner; empty/null = follow owner grants
    allowed_cloud_account_ids: Mapped[list | None] = mapped_column(JSONB)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
