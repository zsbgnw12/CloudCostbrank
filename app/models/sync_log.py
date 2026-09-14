import datetime as dt

from sqlalchemy import String, Integer, Date, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(100))
    # 同一次派发(一次"同步全部"/一次定时/一次手动单源)的多条日志共享一个 batch_id，
    # 前端据此把日志分组成"任务"。历史行为 NULL。
    batch_id: Mapped[str | None] = mapped_column(String(40), index=True)
    start_time: Mapped[dt.datetime] = mapped_column(nullable=False)
    end_time: Mapped[dt.datetime | None] = mapped_column()
    status: Mapped[str | None] = mapped_column(String(15))  # running / success / failed
    query_start_date: Mapped[dt.date | None] = mapped_column(Date)
    query_end_date: Mapped[dt.date | None] = mapped_column(Date)
    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    records_upserted: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)

    # relationships
    data_source = relationship("DataSource", back_populates="sync_logs")
