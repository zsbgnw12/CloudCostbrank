from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AzureConsentInvite(Base):
    __tablename__ = "azure_consent_invites"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    cloud_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cloud_accounts.id"), nullable=True
    )
    # 邀请创建时指定：consent 成功后自动把发现的订阅建成 Project 并挂到这个 SupplySource。
    # 为 None 时 verify 只记录订阅到 secret_data，不自动建服务账号（向后兼容旧 invite）。
    supply_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("supply_sources.id"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    __table_args__ = (
        Index("ix_azure_consent_invites_status", "status"),
        Index("ix_azure_consent_invites_expires_at", "expires_at"),
    )
