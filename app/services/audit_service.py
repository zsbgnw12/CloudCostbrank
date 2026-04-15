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
):
    db.add(OperationLog(
        operator=operator,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        before_data=before_data,
        after_data=after_data,
    ))
    await db.flush()
