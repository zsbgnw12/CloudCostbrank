"""Exchange rates API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.exchange_rate import ExchangeRate
from app.schemas.billing import ExchangeRateCreate, ExchangeRateUpdate, ExchangeRateRead

router = APIRouter()


@router.get("/", response_model=list[ExchangeRateRead])
async def list_rates(
    date: str | None = None,
    from_currency: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ExchangeRate).order_by(ExchangeRate.date.desc())
    if date:
        import datetime as dt
        stmt = stmt.where(ExchangeRate.date == dt.date.fromisoformat(date))
    if from_currency:
        stmt = stmt.where(ExchangeRate.from_currency == from_currency)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=ExchangeRateRead, status_code=201)
async def create_rate(body: ExchangeRateCreate, db: AsyncSession = Depends(get_db)):
    rate = ExchangeRate(**body.model_dump())
    db.add(rate)
    await db.commit()
    await db.refresh(rate)
    return rate


@router.put("/{rate_id}", response_model=ExchangeRateRead)
async def update_rate(rate_id: int, body: ExchangeRateUpdate, db: AsyncSession = Depends(get_db)):
    rate = await db.get(ExchangeRate, rate_id)
    if not rate:
        raise HTTPException(404, "Exchange rate not found")
    rate.rate = body.rate
    await db.commit()
    await db.refresh(rate)
    return rate
