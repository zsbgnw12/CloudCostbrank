"""供应商 + 货源 CRUD。货源 (supply_sources) 为云类型 provider 的唯一业务来源。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.project import Project
from app.models.supplier import Supplier
from app.models.supply_source import SupplySource
from app.services.default_supply_sources import RESERVED_UNASSIGNED_SUPPLIER_NAME

# 权限约定：
#   - 查看 / 创建 / 编辑：cloud_admin + cloud_ops（运营人员日常工作）
#   - 删除：仅 cloud_admin（防止 ops 误删业务配置）
# router 级默认放给 ops+admin；DELETE 端点单独加 require_roles("cloud_admin") 覆盖。
router = APIRouter(dependencies=[Depends(require_roles("cloud_admin", "cloud_ops"))])


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class SupplierCreate(BaseModel):
    name: str


class SupplierUpdate(BaseModel):
    name: str


class SupplySourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    supplier_id: int
    supplier_name: str | None = None
    provider: str
    account_count: int = 0


class SupplySourceCreate(BaseModel):
    provider: str  # aws / gcp / azure


@router.get("/", response_model=list[SupplierRead])
async def list_suppliers(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Supplier).order_by(Supplier.name))
    return list(r.scalars().all())


@router.post("/", response_model=SupplierRead, status_code=201)
async def create_supplier(body: SupplierCreate, db: AsyncSession = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    dup = (await db.execute(
        select(Supplier).where(Supplier.name == name).limit(1)
    )).scalars().first()
    if dup:
        raise HTTPException(409, f"已存在同名供应商「{name}」(id={dup.id})")
    su = Supplier(name=name)
    db.add(su)
    await db.flush()
    await db.refresh(su)
    await db.commit()
    return su


@router.patch("/{supplier_id}", response_model=SupplierRead)
async def update_supplier(supplier_id: int, body: SupplierUpdate, db: AsyncSession = Depends(get_db)):
    s = await db.get(Supplier, supplier_id)
    if not s:
        raise HTTPException(404, "供应商不存在")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if s.name == RESERVED_UNASSIGNED_SUPPLIER_NAME and name != RESERVED_UNASSIGNED_SUPPLIER_NAME:
        raise HTTPException(400, "系统保留供应商不可改名")
    if s.name != RESERVED_UNASSIGNED_SUPPLIER_NAME and name == RESERVED_UNASSIGNED_SUPPLIER_NAME:
        raise HTTPException(400, f"名称「{RESERVED_UNASSIGNED_SUPPLIER_NAME}」为系统保留")
    if name != s.name:
        dup = (await db.execute(
            select(Supplier).where(Supplier.name == name, Supplier.id != supplier_id).limit(1)
        )).scalars().first()
        if dup:
            raise HTTPException(409, f"已存在同名供应商「{name}」(id={dup.id})")
    s.name = name
    await db.commit()
    await db.refresh(s)
    return s


@router.delete(
    "/{supplier_id}",
    status_code=204,
    dependencies=[Depends(require_roles("cloud_admin"))],
)
async def delete_supplier(supplier_id: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(Supplier, supplier_id)
    if not s:
        raise HTTPException(404, "供应商不存在")
    if s.name == RESERVED_UNASSIGNED_SUPPLIER_NAME:
        raise HTTPException(400, "系统保留供应商不可删除")
    cnt = (
        await db.execute(
            select(func.count())
            .select_from(Project)
            .join(SupplySource, Project.supply_source_id == SupplySource.id)
            .where(SupplySource.supplier_id == supplier_id)
        )
    ).scalar_one()
    if cnt and cnt > 0:
        raise HTTPException(409, "该供应商下仍有服务账号，无法删除")
    await db.delete(s)
    await db.commit()


@router.get("/{supplier_id}/supply-sources", response_model=list[SupplySourceRead])
async def list_supply_sources(supplier_id: int, db: AsyncSession = Depends(get_db)):
    s = await db.get(Supplier, supplier_id)
    if not s:
        raise HTTPException(404, "供应商不存在")
    ss_rows = (await db.execute(select(SupplySource).where(SupplySource.supplier_id == supplier_id))).scalars().all()
    out: list[SupplySourceRead] = []
    for ss in ss_rows:
        n = (
            await db.execute(select(func.count()).select_from(Project).where(Project.supply_source_id == ss.id))
        ).scalar_one()
        out.append(
            SupplySourceRead(
                id=ss.id,
                supplier_id=ss.supplier_id,
                supplier_name=s.name,
                provider=ss.provider,
                account_count=int(n or 0),
            )
        )
    return sorted(out, key=lambda x: x.provider)


@router.post("/{supplier_id}/supply-sources", response_model=SupplySourceRead, status_code=201)
async def create_supply_source(supplier_id: int, body: SupplySourceCreate, db: AsyncSession = Depends(get_db)):
    s = await db.get(Supplier, supplier_id)
    if not s:
        raise HTTPException(404, "供应商不存在")
    p = body.provider.strip().lower()
    if p not in ("aws", "gcp", "azure", "taiji"):
        raise HTTPException(400, "provider 须为 aws / gcp / azure / taiji")
    exists = (
        await db.execute(
            select(SupplySource).where(SupplySource.supplier_id == supplier_id, SupplySource.provider == p)
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"该供应商已存在 {p.upper()} 货源")
    ss = SupplySource(supplier_id=supplier_id, provider=p)
    db.add(ss)
    await db.commit()
    await db.refresh(ss)
    return SupplySourceRead(
        id=ss.id, supplier_id=ss.supplier_id, supplier_name=s.name, provider=ss.provider, account_count=0,
    )


@router.delete(
    "/supply-sources/{supply_source_id}",
    status_code=204,
    dependencies=[Depends(require_roles("cloud_admin"))],
)
async def delete_supply_source(
    supply_source_id: int,
    db: AsyncSession = Depends(get_db),
):
    ss = await db.get(SupplySource, supply_source_id)
    if not ss:
        raise HTTPException(404, "货源不存在")
    n = (
        await db.execute(select(func.count()).select_from(Project).where(Project.supply_source_id == supply_source_id))
    ).scalar_one()
    if n and n > 0:
        raise HTTPException(409, "该货源下仍有服务账号，无法删除")
    await db.delete(ss)
    await db.commit()


@router.get("/supply-sources/all", response_model=list[SupplySourceRead])
async def list_all_supply_sources(
    supplier_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """可选按供应商筛选，供下拉使用。"""
    stmt = select(SupplySource, Supplier.name.label("supplier_name")).join(
        Supplier, SupplySource.supplier_id == Supplier.id
    )
    if supplier_id is not None:
        stmt = stmt.where(SupplySource.supplier_id == supplier_id)
    stmt = stmt.order_by(SupplySource.supplier_id, SupplySource.provider)
    rows = (await db.execute(stmt)).all()
    out: list[SupplySourceRead] = []
    for ss, sname in rows:
        n = (
            await db.execute(select(func.count()).select_from(Project).where(Project.supply_source_id == ss.id))
        ).scalar_one()
        out.append(
            SupplySourceRead(
                id=ss.id,
                supplier_id=ss.supplier_id,
                supplier_name=sname,
                provider=ss.provider,
                account_count=int(n or 0),
            )
        )
    return out
