import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.report import Report

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportOut(BaseModel):
    id: int
    status: str
    triggered_by: str
    tickers_requested: list[str]
    tickers_ok: list[str]
    sources_failed: list[str]
    duration_seconds: int | None
    created_at: str

    model_config = {"from_attributes": True}


def _parse(row: Report) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "triggered_by": row.triggered_by,
        "tickers_requested": json.loads(row.tickers_requested or "[]"),
        "tickers_ok": json.loads(row.tickers_ok or "[]"),
        "sources_failed": json.loads(row.sources_failed or "[]"),
        "duration_seconds": row.duration_seconds,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


@router.get("")
async def list_reports(limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = await db.execute(
        select(Report).order_by(Report.created_at.desc()).limit(limit)
    )
    return [_parse(r) for r in rows.scalars()]
