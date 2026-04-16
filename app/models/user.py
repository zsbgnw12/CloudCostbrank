"""User model — local shadow of Casdoor identity.

We never store passwords here. Casdoor is the source of truth for authentication;
this table is the anchor for business FKs (grants, api keys, audit logs).
"""

from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Casdoor identity — `sub` claim is the stable unique id across sessions.
    casdoor_sub: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(500))

    # Cached from Casdoor on each login; the ultimate source is Casdoor userinfo.
    # Example: ["cloud_admin", "cloud_ops"].
    roles: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # Soft disable — independent from Casdoor's own disabled flag so ops can block a
    # user locally without waiting for Casdoor sync.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
