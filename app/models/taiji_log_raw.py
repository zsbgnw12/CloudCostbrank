"""Taiji 原始请求日志 — Push 模式下的单条请求留档。

业务流：
    taiji → POST /api/metering/taiji/ingest → 去重入这张表 → 按涉及日期重算 billing_data + token_usage
    每日 Celery GC 任务清理 30 天前的行。

幂等主键：(data_source_id, id) —— taiji 系统内 id 唯一，但允许 cloudcost 接多个 taiji 实例。
"""

import datetime as dt

from sqlalchemy import (
    String,
    Integer,
    SmallInteger,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TaijiLogRaw(Base):
    __tablename__ = "taiji_log_raw"
    __table_args__ = (
        PrimaryKeyConstraint("data_source_id", "id", name="pk_taiji_log_raw"),
        Index("ix_taiji_log_raw_ds_date", "data_source_id", "date"),
        Index("ix_taiji_log_raw_ds_ingested", "data_source_id", "ingested_at"),
    )

    # taiji 自己的主键（每个 DS 内唯一；+ data_source_id 防多实例冲突）
    id: Mapped[int] = mapped_column(BigInteger, nullable=False, autoincrement=False)
    data_source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False,
    )

    # 从 created_at 派生的 UTC 日期，方便按天筛选 / GC / 聚合
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)

    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)  # taiji 原始 unix 秒
    type: Mapped[int | None] = mapped_column(SmallInteger)

    user_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(200))
    token_id: Mapped[int] = mapped_column(Integer, nullable=False)
    token_name: Mapped[str | None] = mapped_column(String(200))

    channel_id: Mapped[int | None] = mapped_column(Integer)
    channel_name: Mapped[str | None] = mapped_column(String(200))

    model_name: Mapped[str] = mapped_column(String(200), nullable=False)

    quota: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    use_time: Mapped[int | None] = mapped_column(Integer)
    is_stream: Mapped[int | None] = mapped_column(SmallInteger)

    # 原样保留 taiji other 字段（JSONB；taiji 可能发字符串也可能发对象，入库前统一成 dict）
    other: Mapped[dict | None] = mapped_column(JSONB)

    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
