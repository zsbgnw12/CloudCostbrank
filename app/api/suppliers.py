"""供应商 + 货源 CRUD。货源 (supply_sources) 为云类型 provider 的唯一业务来源。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.entity import Entity
from app.models.project import Project
from app.models.supplier import Supplier
from app.models.supply_source import SupplySource
from app.services.default_supply_sources import RESERVED_UNASSIGNED_SUPPLIER_NAME

# 权限约定（router 级不锁,由 main.py 的 _cloud("suppliers") 拦截到云管角色,
# 这里只对写操作单独加 admin 锁:供应商和货源是跨云的全局元数据,创建删除影响所有云的归口,只 admin 可改）：
#   - GET 列表/详情:任何云管角色(给前端"添加云账号"对话框的下拉选项用)
#   - POST/PATCH/DELETE:仅 cloud_admin
router = APIRouter()


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


class EntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    supply_source_id: int
    supplier_id: int | None = None
    supplier_name: str | None = None
    provider: str | None = None
    name: str
    note: str | None = None
    account_count: int = 0


class EntityCreate(BaseModel):
    name: str
    note: str | None = None


class EntityUpdate(BaseModel):
    name: str | None = None
    note: str | None = None


@router.get("/", response_model=list[SupplierRead])
async def list_suppliers(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Supplier).order_by(Supplier.name))
    return list(r.scalars().all())


@router.post("/", response_model=SupplierRead, status_code=201,
             dependencies=[Depends(require_roles("cloud_admin"))])
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


@router.patch("/{supplier_id}", response_model=SupplierRead,
              dependencies=[Depends(require_roles("cloud_admin"))])
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


@router.post("/{supplier_id}/supply-sources", response_model=SupplySourceRead, status_code=201,
             dependencies=[Depends(require_roles("cloud_admin"))])
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


# ─── Entity（主体）CRUD ─────────────────────────────────────────────────────
# 层级：suppliers → supply_sources → entities → projects
# 读放给云角色（前端服务账号列表展示要用），写仅 cloud_admin。


async def _entity_to_read(
    db: AsyncSession,
    e: Entity,
    *,
    supplier_id: int | None = None,
    supplier_name: str | None = None,
    provider: str | None = None,
) -> EntityRead:
    if supplier_id is None or supplier_name is None or provider is None:
        ss = await db.get(SupplySource, e.supply_source_id)
        if ss is not None:
            provider = ss.provider
            supplier_id = ss.supplier_id
            sup = await db.get(Supplier, ss.supplier_id)
            supplier_name = sup.name if sup else None
    cnt = (
        await db.execute(select(func.count()).select_from(Project).where(Project.entity_id == e.id))
    ).scalar_one()
    return EntityRead(
        id=e.id,
        supply_source_id=e.supply_source_id,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        provider=provider,
        name=e.name,
        note=e.note,
        account_count=int(cnt or 0),
    )


@router.get("/supply-sources/{supply_source_id}/entities", response_model=list[EntityRead])
async def list_entities(supply_source_id: int, db: AsyncSession = Depends(get_db)):
    ss = await db.get(SupplySource, supply_source_id)
    if not ss:
        raise HTTPException(404, "货源不存在")
    sup = await db.get(Supplier, ss.supplier_id)
    rows = (
        await db.execute(
            select(Entity).where(Entity.supply_source_id == supply_source_id).order_by(Entity.name)
        )
    ).scalars().all()
    return [
        await _entity_to_read(
            db, e,
            supplier_id=ss.supplier_id,
            supplier_name=sup.name if sup else None,
            provider=ss.provider,
        )
        for e in rows
    ]


@router.post(
    "/supply-sources/{supply_source_id}/entities",
    response_model=EntityRead,
    status_code=201,
    dependencies=[Depends(require_roles("cloud_admin"))],
)
async def create_entity(
    supply_source_id: int,
    body: EntityCreate,
    db: AsyncSession = Depends(get_db),
):
    ss = await db.get(SupplySource, supply_source_id)
    if not ss:
        raise HTTPException(404, "货源不存在")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    dup = (
        await db.execute(
            select(Entity).where(
                Entity.supply_source_id == supply_source_id, Entity.name == name
            ).limit(1)
        )
    ).scalars().first()
    if dup:
        raise HTTPException(409, f"该货源下已存在同名主体「{name}」")
    note = (body.note or "").strip() or None
    e = Entity(supply_source_id=supply_source_id, name=name, note=note)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    sup = await db.get(Supplier, ss.supplier_id)
    return await _entity_to_read(
        db, e,
        supplier_id=ss.supplier_id,
        supplier_name=sup.name if sup else None,
        provider=ss.provider,
    )


@router.get("/entities/all", response_model=list[EntityRead])
async def list_all_entities(
    supply_source_id: int | None = Query(None),
    supplier_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """flat 列表，供前端下拉使用。可按 supply_source_id 或 supplier_id 过滤。"""
    stmt = (
        select(Entity, SupplySource, Supplier.name.label("supplier_name"))
        .join(SupplySource, Entity.supply_source_id == SupplySource.id)
        .join(Supplier, SupplySource.supplier_id == Supplier.id)
    )
    if supply_source_id is not None:
        stmt = stmt.where(Entity.supply_source_id == supply_source_id)
    if supplier_id is not None:
        stmt = stmt.where(SupplySource.supplier_id == supplier_id)
    stmt = stmt.order_by(SupplySource.supplier_id, SupplySource.provider, Entity.name)
    rows = (await db.execute(stmt)).all()
    out: list[EntityRead] = []
    for e, ss, sname in rows:
        cnt = (
            await db.execute(select(func.count()).select_from(Project).where(Project.entity_id == e.id))
        ).scalar_one()
        out.append(
            EntityRead(
                id=e.id,
                supply_source_id=e.supply_source_id,
                supplier_id=ss.supplier_id,
                supplier_name=sname,
                provider=ss.provider,
                name=e.name,
                note=e.note,
                account_count=int(cnt or 0),
            )
        )
    return out


@router.patch(
    "/entities/{entity_id}",
    response_model=EntityRead,
    dependencies=[Depends(require_roles("cloud_admin"))],
)
async def update_entity(
    entity_id: int,
    body: EntityUpdate,
    db: AsyncSession = Depends(get_db),
):
    e = await db.get(Entity, entity_id)
    if not e:
        raise HTTPException(404, "主体不存在")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "名称不能为空")
        if name != e.name:
            dup = (
                await db.execute(
                    select(Entity).where(
                        Entity.supply_source_id == e.supply_source_id,
                        Entity.name == name,
                        Entity.id != entity_id,
                    ).limit(1)
                )
            ).scalars().first()
            if dup:
                raise HTTPException(409, f"该货源下已存在同名主体「{name}」")
            e.name = name
    if body.note is not None:
        n = body.note.strip()
        e.note = n or None
    await db.commit()
    await db.refresh(e)
    return await _entity_to_read(db, e)


@router.delete(
    "/entities/{entity_id}",
    status_code=204,
    dependencies=[Depends(require_roles("cloud_admin"))],
)
async def delete_entity(entity_id: int, db: AsyncSession = Depends(get_db)):
    e = await db.get(Entity, entity_id)
    if not e:
        raise HTTPException(404, "主体不存在")
    # Project.entity_id 已是 ON DELETE SET NULL，主体下若仍有服务账号会被打回未分配。
    # 但为防止误删，要求先 detach：服务账号还挂着就报 409，保持与 supplier/supply-source 一致的安全策略。
    cnt = (
        await db.execute(select(func.count()).select_from(Project).where(Project.entity_id == entity_id))
    ).scalar_one()
    if cnt and cnt > 0:
        raise HTTPException(409, "该主体下仍有服务账号，先解绑或迁移后再删除")
    await db.delete(e)
    await db.commit()
