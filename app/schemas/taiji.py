"""Taiji Push ingest schemas."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaijiLogIn(BaseModel):
    """来自 taiji 的原始请求日志。字段名沿用 taiji 自己的列名。"""
    model_config = ConfigDict(extra="ignore")  # taiji 加新字段不破坏

    id: int = Field(..., description="taiji 原始主键，去重用")
    user_id: int | None = None
    created_at: int = Field(..., description="Unix 秒时间戳（UTC）")
    type: int | None = None  # 一般是 2 (consume)

    username: str | None = None
    token_id: int
    token_name: str | None = None

    channel_id: int | None = None
    channel_name: str | None = None

    model_name: str
    quota: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    use_time: int | None = None
    is_stream: int | None = None

    # taiji 的 other 字段：可能是 string（JSON 字符串）或 object
    other: Any = None


class TaijiIngestRequest(BaseModel):
    logs: list[TaijiLogIn] = Field(..., min_length=1, max_length=2000)


class TaijiIngestResponse(BaseModel):
    received: int
    stored_new: int        # 原始表新增行数
    deduped: int           # 重复 id 被跳过的行数
    dates_reaggregated: list[str]
    billing_rows_upserted: int
    token_usage_rows_upserted: int
    projects_created: int
