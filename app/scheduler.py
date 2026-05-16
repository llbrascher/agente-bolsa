"""Scheduler singleton baseado em APScheduler.

Usa AsyncIOScheduler (integra com o event loop do FastAPI) e persiste os jobs
no banco via SQLAlchemyJobStore com a URL síncrona equivalente ao DATABASE_URL.
"""

import logging

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

logger = logging.getLogger(__name__)

# APScheduler usa SQLAlchemy síncrono internamente — converte a URL async para sync
def _sync_url(url: str) -> str:
    return url.replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql")


scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=_sync_url(settings.database_url))},
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    timezone="America/Sao_Paulo",
)


# ── Job executado pelo scheduler ──────────────────────────────────────────────

async def scheduled_report_job(schedule_id: int) -> None:
    """Carrega empresas ativas do banco e dispara o relatório."""
    from app.database import AsyncSessionLocal
    from app.models.company import Company
    from app.tasks.report_task import run_report
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(Company).where(Company.active == True))  # noqa: E712
        companies = [{"ticker": c.ticker, "name": c.name} for c in rows.scalars()]

    if not companies:
        logger.warning("[scheduler] Nenhuma empresa ativa — relatório cancelado")
        return

    logger.info("[scheduler] Disparando relatório (schedule_id=%d, %d empresa(s))", schedule_id, len(companies))

    async with AsyncSessionLocal() as db:
        await run_report(companies, triggered_by=f"schedule:{schedule_id}", db=db)


# ── Sincronização schedules DB ↔ APScheduler ─────────────────────────────────

def job_id(schedule_id: int) -> str:
    return f"report_schedule_{schedule_id}"


def register_schedule(schedule_id: int, hour: int, minute: int) -> None:
    """Cria ou atualiza um job CronTrigger para o schedule dado."""
    jid = job_id(schedule_id)
    if scheduler.get_job(jid):
        scheduler.reschedule_job(jid, trigger="cron", hour=hour, minute=minute)
        logger.info("Schedule %d atualizado para %02d:%02d", schedule_id, hour, minute)
    else:
        scheduler.add_job(
            scheduled_report_job,
            trigger="cron",
            hour=hour,
            minute=minute,
            id=jid,
            name=f"Relatório {hour:02d}:{minute:02d}",
            kwargs={"schedule_id": schedule_id},
        )
        logger.info("Schedule %d registrado para %02d:%02d", schedule_id, hour, minute)


def remove_schedule(schedule_id: int) -> None:
    jid = job_id(schedule_id)
    if scheduler.get_job(jid):
        scheduler.remove_job(jid)
        logger.info("Schedule %d removido", schedule_id)


async def load_schedules_from_db() -> None:
    """Chamado no startup: sincroniza todos os schedules ativos do banco."""
    from app.database import AsyncSessionLocal
    from app.models.schedule import Schedule
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(Schedule).where(Schedule.active == True))  # noqa: E712
        schedules = rows.scalars().all()

    for s in schedules:
        register_schedule(s.id, s.hour, s.minute)

    logger.info("[scheduler] %d schedule(s) carregado(s) do banco", len(schedules))
