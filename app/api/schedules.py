import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schedule import Schedule

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

MAX_SCHEDULES = 4


class ScheduleIn(BaseModel):
    hour: int
    minute: int = 0
    label: str | None = None

    @field_validator("hour")
    @classmethod
    def validate_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError("Hora deve ser entre 0 e 23")
        return v

    @field_validator("minute")
    @classmethod
    def validate_minute(cls, v: int) -> int:
        if not 0 <= v <= 59:
            raise ValueError("Minuto deve ser entre 0 e 59")
        return v


class ScheduleOut(BaseModel):
    id: int
    hour: int
    minute: int
    active: bool
    label: str | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Schedule).order_by(Schedule.hour, Schedule.minute))
    return rows.scalars().all()


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(body: ScheduleIn, db: AsyncSession = Depends(get_db)):
    from app.scheduler import register_schedule

    count = await db.execute(select(Schedule))
    if len(count.scalars().all()) >= MAX_SCHEDULES:
        raise HTTPException(400, f"Limite de {MAX_SCHEDULES} horários atingido")

    schedule = Schedule(hour=body.hour, minute=body.minute, label=body.label)
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    register_schedule(schedule.id, schedule.hour, schedule.minute)
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: int, body: ScheduleIn, db: AsyncSession = Depends(get_db)):
    from app.scheduler import register_schedule, remove_schedule

    row = await db.get(Schedule, schedule_id)
    if not row:
        raise HTTPException(404, "Horário não encontrado")

    row.hour = body.hour
    row.minute = body.minute
    row.label = body.label
    await db.commit()
    await db.refresh(row)

    if row.active:
        register_schedule(row.id, row.hour, row.minute)
    else:
        remove_schedule(row.id)
    return row


@router.patch("/{schedule_id}/toggle", response_model=ScheduleOut)
async def toggle_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    from app.scheduler import register_schedule, remove_schedule

    row = await db.get(Schedule, schedule_id)
    if not row:
        raise HTTPException(404, "Horário não encontrado")

    row.active = not row.active
    await db.commit()
    await db.refresh(row)

    if row.active:
        register_schedule(row.id, row.hour, row.minute)
    else:
        remove_schedule(row.id)
    return row


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    from app.scheduler import remove_schedule

    row = await db.get(Schedule, schedule_id)
    if not row:
        raise HTTPException(404, "Horário não encontrado")

    remove_schedule(row.id)
    await db.delete(row)
    await db.commit()


@router.post("/trigger", status_code=202)
async def trigger_now(db: AsyncSession = Depends(get_db)):
    """Dispara o relatório imediatamente, fora do agendamento."""
    from app.models.company import Company
    from app.tasks.report_task import run_report
    from sqlalchemy import select

    rows = await db.execute(select(Company).where(Company.active == True))  # noqa: E712
    companies = [{"ticker": c.ticker, "name": c.name} for c in rows.scalars()]

    if not companies:
        raise HTTPException(400, "Nenhuma empresa ativa cadastrada")

    asyncio.create_task(run_report(companies, triggered_by="manual:api", db=db))
    return {"status": "dispatched", "companies": len(companies)}
