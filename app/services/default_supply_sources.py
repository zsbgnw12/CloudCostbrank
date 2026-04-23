"""自动发现 GCP 项目挂靠的默认供应商 + 货源（固定名称，与前端/迁移约定一致）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.supplier import Supplier
from app.models.supply_source import SupplySource

# 系统保留：GCP 未建档项目默认挂靠此供应商（不可改名，见 suppliers API + 前端）
RESERVED_UNASSIGNED_SUPPLIER_NAME = "未分配资源组"


async def ensure_other_gcp_supply_source_id(db: AsyncSession) -> tuple[int, str]:
    """确保存在保留供应商及其 GCP 货源。返回 (supply_sources.id, 供应商名称)。"""
    r = await db.execute(
        select(Supplier).where(Supplier.name == RESERVED_UNASSIGNED_SUPPLIER_NAME).limit(1)
    )
    su = r.scalars().first()
    if not su:
        su = Supplier(name=RESERVED_UNASSIGNED_SUPPLIER_NAME)
        db.add(su)
        await db.flush()

    r2 = await db.execute(
        select(SupplySource).where(
            SupplySource.supplier_id == su.id,
            SupplySource.provider == "gcp",
        )
    )
    ss = r2.scalars().first()
    if not ss:
        ss = SupplySource(supplier_id=su.id, provider="gcp")
        db.add(ss)
        await db.flush()
    return ss.id, su.name


def ensure_other_gcp_supply_source_id_sync(session: Session) -> tuple[int, str]:
    """同步 Session 版本（Celery / 同步任务）。"""
    return _ensure_reserved_supply_source_sync(session, provider="gcp")


def ensure_other_taiji_supply_source_id_sync(session: Session) -> tuple[int, str]:
    """Taiji 新 token 自动发现时挂靠的默认货源（同 GCP 的 '未分配资源组'）。"""
    return _ensure_reserved_supply_source_sync(session, provider="taiji")


def _ensure_reserved_supply_source_sync(session: Session, *, provider: str) -> tuple[int, str]:
    su = session.execute(
        select(Supplier).where(Supplier.name == RESERVED_UNASSIGNED_SUPPLIER_NAME).limit(1)
    ).scalars().first()
    if not su:
        su = Supplier(name=RESERVED_UNASSIGNED_SUPPLIER_NAME)
        session.add(su)
        session.flush()

    ss = session.execute(
        select(SupplySource).where(
            SupplySource.supplier_id == su.id,
            SupplySource.provider == provider,
        )
    ).scalars().first()
    if not ss:
        ss = SupplySource(supplier_id=su.id, provider=provider)
        session.add(ss)
        session.flush()
    return ss.id, su.name
