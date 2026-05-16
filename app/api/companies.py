from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.company import Company

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyIn(BaseModel):
    ticker: str
    name: str
    notes: str | None = None


class CompanyOut(BaseModel):
    id: int
    ticker: str
    name: str
    active: bool
    notes: str | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[CompanyOut])
async def list_companies(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Company).order_by(Company.ticker))
    return rows.scalars().all()


@router.post("", response_model=CompanyOut, status_code=201)
async def create_company(body: CompanyIn, db: AsyncSession = Depends(get_db)):
    ticker = body.ticker.upper().strip()
    existing = await db.execute(select(Company).where(Company.ticker == ticker))
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Ticker {ticker} já cadastrado")

    company = Company(ticker=ticker, name=body.name.strip(), notes=body.notes)
    db.add(company)
    await db.commit()
    await db.refresh(company)
    return company


@router.patch("/{company_id}", response_model=CompanyOut)
async def update_company(company_id: int, body: CompanyIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(Company, company_id)
    if not row:
        raise HTTPException(404, "Empresa não encontrada")

    row.ticker = body.ticker.upper().strip()
    row.name = body.name.strip()
    row.notes = body.notes
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/{company_id}/toggle", response_model=CompanyOut)
async def toggle_company(company_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(Company, company_id)
    if not row:
        raise HTTPException(404, "Empresa não encontrada")

    row.active = not row.active
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{company_id}", status_code=204)
async def delete_company(company_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(Company, company_id)
    if not row:
        raise HTTPException(404, "Empresa não encontrada")

    await db.delete(row)
    await db.commit()
