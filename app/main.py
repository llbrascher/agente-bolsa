from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.scheduler import load_schedules_from_db, scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.start()
    await load_schedules_from_db()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Agente Bolsa",
    description="Monitoramento de ações B3 com IA",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

from app.api import companies, pages, reports, schedules  # noqa: E402

app.include_router(pages.router)      # páginas HTML + partials HTMX
app.include_router(companies.router)  # JSON API
app.include_router(schedules.router)  # JSON API
app.include_router(reports.router)    # JSON API


@app.get("/health")
async def health():
    jobs = scheduler.get_jobs()
    return {
        "status": "ok",
        "version": "0.1.0",
        "scheduled_jobs": len(jobs),
    }
