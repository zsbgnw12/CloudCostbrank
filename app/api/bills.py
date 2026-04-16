"""Monthly bills API."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.monthly_bill import MonthlyBill
from app.schemas.billing import MonthlyBillRead, MonthlyBillAdjust, MonthlyBillGenerate
from app.services.bill_service import generate_bills
from app.services.audit_service import log_operation

# Monthly bills are aggregated (no cloud_account_id column) — restrict to
# finance + admin rather than trying to split by account.
router = APIRouter(dependencies=[Depends(require_roles("cloud_finance"))])


@router.get("/", response_model=list[MonthlyBillRead])
async def list_bills(
    month: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(MonthlyBill).order_by(MonthlyBill.id.desc())
    if month:
        stmt = stmt.where(MonthlyBill.month == month)
    if status:
        stmt = stmt.where(MonthlyBill.status == status)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/generate")
async def generate(body: MonthlyBillGenerate, db: AsyncSession = Depends(get_db)):
    count = await generate_bills(db, body.month)
    return {"generated": count, "month": body.month}


@router.get("/{bill_id}", response_model=MonthlyBillRead)
async def get_bill(bill_id: int, db: AsyncSession = Depends(get_db)):
    bill = await db.get(MonthlyBill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    return bill


@router.put("/{bill_id}/adjust", response_model=MonthlyBillRead)
async def adjust_bill(bill_id: int, body: MonthlyBillAdjust, db: AsyncSession = Depends(get_db)):
    bill = await db.get(MonthlyBill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    if bill.status not in ("draft", "confirmed"):
        raise HTTPException(400, f"Cannot adjust bill in '{bill.status}' status, only draft or confirmed bills can be adjusted")
    before = {"adjustment": float(bill.adjustment), "final_cost": float(bill.final_cost), "notes": bill.notes}
    bill.adjustment = body.adjustment
    if body.notes:
        bill.notes = body.notes
    bill.final_cost = bill.original_cost * bill.markup_rate + bill.adjustment
    await log_operation(db, action="adjust_bill", target_type="monthly_bill", target_id=bill_id,
                        before_data=before,
                        after_data={"adjustment": float(bill.adjustment), "final_cost": float(bill.final_cost), "notes": bill.notes})
    await db.commit()
    return bill


@router.post("/{bill_id}/confirm", response_model=MonthlyBillRead)
async def confirm_bill(bill_id: int, db: AsyncSession = Depends(get_db)):
    bill = await db.get(MonthlyBill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    if bill.status != "draft":
        raise HTTPException(400, f"Cannot confirm bill in '{bill.status}' status, only draft bills can be confirmed")
    bill.status = "confirmed"
    bill.confirmed_at = dt.datetime.utcnow()
    await log_operation(db, action="confirm_bill", target_type="monthly_bill", target_id=bill_id,
                        after_data={"status": "confirmed", "confirmed_at": bill.confirmed_at.isoformat()})
    await db.commit()
    return bill


@router.post("/{bill_id}/mark-paid", response_model=MonthlyBillRead)
async def mark_paid(bill_id: int, db: AsyncSession = Depends(get_db)):
    bill = await db.get(MonthlyBill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    if bill.status != "confirmed":
        raise HTTPException(400, f"Cannot mark bill as paid in '{bill.status}' status, only confirmed bills can be marked as paid")
    bill.status = "paid"
    await log_operation(db, action="mark_paid", target_type="monthly_bill", target_id=bill_id,
                        after_data={"status": "paid"})
    await db.commit()
    return bill


@router.delete("/{bill_id}", status_code=204)
async def delete_bill(bill_id: int, db: AsyncSession = Depends(get_db)):
    bill = await db.get(MonthlyBill, bill_id)
    if not bill:
        raise HTTPException(404, "Bill not found")
    if bill.status != "draft":
        raise HTTPException(400, f"Cannot delete bill in '{bill.status}' status, only draft bills can be deleted")
    await db.delete(bill)
    await db.commit()


@router.post("/regenerate")
async def regenerate(body: MonthlyBillGenerate, db: AsyncSession = Depends(get_db)):
    # Delete existing draft bills for the month
    stmt = select(MonthlyBill).where(MonthlyBill.month == body.month, MonthlyBill.status == "draft")
    result = await db.execute(stmt)
    drafts = result.scalars().all()
    deleted = len(drafts)
    for bill in drafts:
        await db.delete(bill)
    # 与 generate 同一事务：失败时由 get_db 回滚，不会只删草稿不生成
    count = await generate_bills(db, body.month)
    return {"deleted_drafts": deleted, "generated": count, "month": body.month}
