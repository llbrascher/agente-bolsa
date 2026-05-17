"""Rotas que retornam HTML — páginas completas e partials para HTMX."""

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.company import Company
from app.models.report import Report
from app.models.schedule import Schedule
from app.scheduler import register_schedule, remove_schedule, scheduler

router = APIRouter(tags=["pages"])
# Starlette 1.0: TemplateResponse(request, name, context) — request fora do context
templates = Jinja2Templates(directory="frontend/templates")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _companies(db: AsyncSession) -> list[Company]:
    rows = await db.execute(select(Company).order_by(Company.ticker))
    return rows.scalars().all()

async def _schedules(db: AsyncSession) -> list[Schedule]:
    rows = await db.execute(select(Schedule).order_by(Schedule.hour, Schedule.minute))
    return rows.scalars().all()

async def _reports(db: AsyncSession, limit: int = 50) -> list[dict]:
    rows = await db.execute(select(Report).order_by(Report.created_at.desc()).limit(limit))
    return [
        {
            "id": r.id,
            "status": r.status,
            "triggered_by": r.triggered_by,
            "tickers": json.loads(r.tickers_requested or "[]"),
            "tickers_ok": json.loads(r.tickers_ok or "[]"),
            "sources_failed": json.loads(r.sources_failed or "[]"),
            "duration_seconds": r.duration_seconds,
            "created_at": r.created_at,
        }
        for r in rows.scalars()
    ]

def _next_jobs() -> list[dict]:
    return [
        {"name": j.name, "next_run": j.next_run_time}
        for j in scheduler.get_jobs()
        if j.next_run_time
    ]

def resp(request: Request, name: str, ctx: dict):
    """Atalho para TemplateResponse com a nova API do Starlette 1.0."""
    return templates.TemplateResponse(request, name, ctx)


# ── Páginas completas ─────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    reports = await _reports(db, limit=5)
    return resp(request, "dashboard.html", {
        "page": "dashboard",
        "recent_reports": reports,
        "next_jobs": _next_jobs(),
        "total_companies": len(await _companies(db)),
        "total_schedules": len(await _schedules(db)),
    })


@router.get("/empresas", response_class=HTMLResponse)
async def page_companies(request: Request, db: AsyncSession = Depends(get_db)):
    return resp(request, "companies.html", {
        "page": "empresas",
        "companies": await _companies(db),
    })


@router.get("/horarios", response_class=HTMLResponse)
async def page_schedules(request: Request, db: AsyncSession = Depends(get_db)):
    schedules = await _schedules(db)
    return resp(request, "schedules.html", {
        "page": "horarios",
        "schedules": schedules,
        "max_reached": len(schedules) >= 4,
    })


@router.get("/historico", response_class=HTMLResponse)
async def page_history(request: Request, db: AsyncSession = Depends(get_db)):
    return resp(request, "history.html", {
        "page": "historico",
        "reports": await _reports(db),
    })


# ── Partials HTMX — Empresas ──────────────────────────────────────────────────

@router.get("/partials/companies", response_class=HTMLResponse)
async def partial_companies(request: Request, db: AsyncSession = Depends(get_db)):
    return resp(request, "partials/companies_list.html", {
        "companies": await _companies(db),
    })


@router.post("/htmx/companies", response_class=HTMLResponse)
async def htmx_create_company(
    request: Request,
    ticker: str = Form(...),
    name: str = Form(...),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    ticker = ticker.upper().strip()
    existing = await db.execute(select(Company).where(Company.ticker == ticker))
    if not existing.scalar_one_or_none():
        db.add(Company(ticker=ticker, name=name.strip(), notes=notes or None))
        await db.commit()

    return resp(request, "partials/companies_list.html", {
        "companies": await _companies(db),
    })


@router.delete("/htmx/companies/{company_id}", response_class=HTMLResponse)
async def htmx_delete_company(
    request: Request, company_id: int, db: AsyncSession = Depends(get_db)
):
    row = await db.get(Company, company_id)
    if row:
        await db.delete(row)
        await db.commit()

    return resp(request, "partials/companies_list.html", {
        "companies": await _companies(db),
    })


@router.patch("/htmx/companies/{company_id}/toggle", response_class=HTMLResponse)
async def htmx_toggle_company(
    request: Request, company_id: int, db: AsyncSession = Depends(get_db)
):
    row = await db.get(Company, company_id)
    if row:
        row.active = not row.active
        await db.commit()

    return resp(request, "partials/companies_list.html", {
        "companies": await _companies(db),
    })


# ── Partials HTMX — Horários ──────────────────────────────────────────────────

@router.get("/partials/schedules", response_class=HTMLResponse)
async def partial_schedules(request: Request, db: AsyncSession = Depends(get_db)):
    schedules = await _schedules(db)
    return resp(request, "partials/schedules_list.html", {
        "schedules": schedules,
        "max_reached": len(schedules) >= 4,
    })


@router.post("/htmx/schedules", response_class=HTMLResponse)
async def htmx_create_schedule(
    request: Request,
    hour: int = Form(...),
    minute: int = Form(0),
    label: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    schedules = await _schedules(db)
    if len(schedules) < 4:
        s = Schedule(hour=hour, minute=minute, label=label or None)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        register_schedule(s.id, s.hour, s.minute)
        schedules = await _schedules(db)

    return resp(request, "partials/schedules_list.html", {
        "schedules": schedules,
        "max_reached": len(schedules) >= 4,
    })


@router.delete("/htmx/schedules/{schedule_id}", response_class=HTMLResponse)
async def htmx_delete_schedule(
    request: Request, schedule_id: int, db: AsyncSession = Depends(get_db)
):
    row = await db.get(Schedule, schedule_id)
    if row:
        remove_schedule(row.id)
        await db.delete(row)
        await db.commit()

    schedules = await _schedules(db)
    return resp(request, "partials/schedules_list.html", {
        "schedules": schedules,
        "max_reached": len(schedules) >= 4,
    })


@router.patch("/htmx/schedules/{schedule_id}/toggle", response_class=HTMLResponse)
async def htmx_toggle_schedule(
    request: Request, schedule_id: int, db: AsyncSession = Depends(get_db)
):
    row = await db.get(Schedule, schedule_id)
    if row:
        row.active = not row.active
        await db.commit()
        if row.active:
            register_schedule(row.id, row.hour, row.minute)
        else:
            remove_schedule(row.id)

    schedules = await _schedules(db)
    return resp(request, "partials/schedules_list.html", {
        "schedules": schedules,
        "max_reached": len(schedules) >= 4,
    })


# ── Cotações ao vivo (HTMX lazy) ─────────────────────────────────────────────

@router.get("/htmx/quotes", response_class=HTMLResponse)
async def htmx_quotes(request: Request, db: AsyncSession = Depends(get_db)):
    import asyncio
    from app.sources.brapi import BrapiSource

    companies = await _companies(db)
    active = [c for c in companies if c.active]

    brapi = BrapiSource()
    results = await asyncio.gather(*[brapi.fetch(c.ticker, c.name) for c in active])

    quotes = []
    for company, result in zip(active, results):
        if result.success:
            quotes.append(result.data)
        else:
            quotes.append({"ticker": company.ticker, "short_name": company.name, "price": None, "change_pct": None, "logo_url": ""})

    return resp(request, "partials/quotes_cards.html", {"quotes": quotes})


# ── Disparo manual via UI ─────────────────────────────────────────────────────

@router.post("/htmx/trigger", response_class=HTMLResponse)
async def htmx_trigger(request: Request, db: AsyncSession = Depends(get_db)):
    import asyncio
    from app.tasks.report_task import run_report

    rows = await db.execute(select(Company).where(Company.active == True))  # noqa: E712
    companies = [{"ticker": c.ticker, "name": c.name} for c in rows.scalars()]

    if companies:
        asyncio.create_task(run_report(companies, triggered_by="manual:ui"))

    return resp(request, "partials/trigger_feedback.html", {
        "dispatched": bool(companies),
        "count": len(companies),
    })
