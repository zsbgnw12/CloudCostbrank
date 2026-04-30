"""Projects CRUD + state transition API with status logging."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_principal, require_roles
from app.auth.principal import Principal
from app.database import get_db
from app.models.project import Project
from app.models.project_assignment_log import ProjectAssignmentLog
from app.models.supplier import Supplier
from app.models.supply_source import SupplySource
from app.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectRead,
    ProjectAssignmentLogRead,
)

router = APIRouter()

STATE_MACHINE = {
    "activate": (["inactive", "standby"], "active"),
    "suspend": (["active", "standby"], "inactive"),
}


def _add_log(db, project, action: str, from_status: str, to_status: str):
    db.add(ProjectAssignmentLog(
        project_id=project.id,
        action=action,
        from_status=from_status,
        to_status=to_status,
    ))


def _to_read(project: Project, ss: SupplySource, su: Supplier) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        supply_source_id=project.supply_source_id,
        provider=ss.provider,
        supplier_name=su.name,
        external_project_id=project.external_project_id,
        data_source_id=project.data_source_id,
        category_id=project.category_id,
        status=project.status,
        notes=project.notes,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/", response_model=list[ProjectRead])
async def list_projects(
    response: Response,
    status: str | None = None,
    provider: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    base = (
        select(Project, SupplySource, Supplier)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .join(Supplier, SupplySource.supplier_id == Supplier.id)
        .where(Project.recycled_at.is_(None))
    )
    if status:
        base = base.where(Project.status == status)
    if provider:
        base = base.where(SupplySource.provider == provider)

    # 过滤后总数(分页前) → 写响应 header,前端读出做分页 UI
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Page-Size"] = str(page_size)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count, X-Page, X-Page-Size"

    stmt = base.order_by(Project.id).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return [_to_read(p, ss, su) for p, ss, su in result.all()]


@router.post("/", response_model=ProjectRead, status_code=201)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_roles("cloud_admin")),
):
    ss = await db.get(SupplySource, body.supply_source_id)
    if not ss:
        raise HTTPException(404, "货源不存在")
    su = await db.get(Supplier, ss.supplier_id)
    if not su:
        raise HTTPException(500, "供应商数据异常")
    project = Project(**body.model_dump())
    db.add(project)
    await db.flush()
    _add_log(db, project, "created", from_status="", to_status="active")
    await db.commit()
    await db.refresh(project)
    return _to_read(project, ss, su)


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    row = await db.execute(
        select(Project, SupplySource, Supplier)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .join(Supplier, SupplySource.supplier_id == Supplier.id)
        .where(Project.id == project_id, Project.recycled_at.is_(None))
    )
    t = row.first()
    if not t:
        raise HTTPException(404, "Project not found")
    project, ss, su = t
    return _to_read(project, ss, su)


@router.put("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_roles("cloud_admin")),
):
    project = await db.get(Project, project_id)
    if not project or project.recycled_at is not None:
        raise HTTPException(404, "Project not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(project, k, v)
    await db.commit()
    await db.refresh(project)
    row = await db.execute(
        select(Project, SupplySource, Supplier)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .join(Supplier, SupplySource.supplier_id == Supplier.id)
        .where(Project.id == project_id)
    )
    p, ss, su = row.one()
    return _to_read(p, ss, su)


@router.post("/{project_id}/activate", response_model=ProjectRead)
async def activate_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_roles("cloud_admin")),
):
    project = await db.get(Project, project_id)
    if not project or project.recycled_at is not None:
        raise HTTPException(404, "Project not found")
    allowed_from, to_state = STATE_MACHINE["activate"]
    if project.status not in allowed_from:
        raise HTTPException(400, f"Cannot activate from '{project.status}'")
    old = project.status
    project.status = to_state
    _add_log(db, project, "activate", old, to_state)
    await db.commit()
    return await get_project(project_id, db)


@router.post("/{project_id}/suspend", response_model=ProjectRead)
async def suspend_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_roles("cloud_admin")),
):
    project = await db.get(Project, project_id)
    if not project or project.recycled_at is not None:
        raise HTTPException(404, "Project not found")
    allowed_from, to_state = STATE_MACHINE["suspend"]
    if project.status not in allowed_from:
        raise HTTPException(400, f"Cannot suspend from '{project.status}'")
    old = project.status
    project.status = to_state
    _add_log(db, project, "suspend", old, to_state)
    await db.commit()
    return await get_project(project_id, db)


@router.get("/{project_id}/assignment-logs", response_model=list[ProjectAssignmentLogRead])
async def assignment_logs(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    project = await db.get(Project, project_id)
    if not project or project.recycled_at is not None:
        raise HTTPException(404, "Project not found")
    r = await db.execute(
        select(ProjectAssignmentLog)
        .where(ProjectAssignmentLog.project_id == project_id)
        .order_by(ProjectAssignmentLog.created_at.desc())
    )
    return list(r.scalars().all())
