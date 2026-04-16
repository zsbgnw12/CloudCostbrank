"""Audit logging utility for sensitive operations."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operation_log import OperationLog


async def log_operation(
    db: AsyncSession,
    *,
    action: str,
    target_type: str,
    target_id: str | int,
    before_data: dict | None = None,
    after_data: dict | None = None,
    operator: str | None = None,
    user_id: int | None = None,
    casdoor_sub: str | None = None,
    auth_method: str | None = None,
    ip: str | None = None,
    trace_id: str | None = None,
):
    db.add(OperationLog(
        operator=operator,
        user_id=user_id,
        casdoor_sub=casdoor_sub,
        auth_method=auth_method,
        ip=ip,
        trace_id=trace_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        before_data=before_data,
        after_data=after_data,
    ))
    await db.flush()
