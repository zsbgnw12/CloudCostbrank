"""Dashboard API routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import dashboard_service

router = APIRouter()


@router.get("/overview")
async def overview(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), db: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_overview(db, month)


@router.get("/trend")
async def trend(
    start: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    end: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    granularity: str = Query("daily", pattern=r"^(daily|weekly|monthly)$"),
    db: AsyncSession = Depends(get_db),
):
    return await dashboard_service.get_trend(db, start, end, granularity)


@router.get("/by-provider")
async def by_provider(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), db: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_by_provider(db, month)


@router.get("/by-category")
async def by_category(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), db: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_by_category(db, month)


@router.get("/by-project")
async def by_project(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await dashboard_service.get_by_project(db, month, limit)


@router.get("/by-service")
async def by_service(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    provider: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await dashboard_service.get_by_service(db, month, provider, limit)


@router.get("/by-region")
async def by_region(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), db: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_by_region(db, month)


@router.get("/top-growth")
async def top_growth(
    period: str = Query("7d"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await dashboard_service.get_top_growth(db, period, limit)


@router.get("/unassigned")
async def unassigned(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), db: AsyncSession = Depends(get_db)):
    return await dashboard_service.get_unassigned(db, month)


@router.get("/bundle")
async def dashboard_bundle(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    granularity: str = Query("daily", pattern=r"^(daily|weekly|monthly)$"),
    service_limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Single response for the home dashboard (one HTTP roundtrip).

    Uses the same service layer as split endpoints. Calls are sequential so one
    AsyncSession is not used concurrently.
    """
    overview = await dashboard_service.get_overview(db, month)
    trend = await dashboard_service.get_trend(db, month, month, granularity)
    by_provider = await dashboard_service.get_by_provider(db, month)
    by_service = await dashboard_service.get_by_service(db, month, None, service_limit)
    return {
        "overview": overview,
        "trend": trend,
        "by_provider": by_provider,
        "by_service": by_service,
    }
