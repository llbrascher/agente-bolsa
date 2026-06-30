"""Orquestrador principal: coleta → IA → e-mail.

Cada empresa é processada independentemente. Se uma fonte falhar, o relatório
continua com os dados disponíveis e registra a falha no histórico.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.summarizer import CompanyData, summarize_company, futures_search_term
from app.email.sender import send_report
from app.models.report import Report
from app.sources.brapi import BrapiSource
from app.sources.google_news import GoogleNewsSource
from app.sources.trends import GoogleTrendsSource
from app.sources.reddit import RedditSource
from app.sources.substack import SubstackSource
from app.sources.tavily import TavilySource
from app.sources.youtube import YouTubeSource

logger = logging.getLogger(__name__)

# Fontes institucionais (Camada 1)
INSTITUTIONAL_SOURCES = [
    BrapiSource(),
    GoogleNewsSource(),
    TavilySource(),
    SubstackSource(),
]

# Fontes de sentimento do varejo (Camada 2)
RETAIL_SOURCES = [
    RedditSource(),
    YouTubeSource(),
    GoogleTrendsSource(),
]

ALL_SOURCES = INSTITUTIONAL_SOURCES + RETAIL_SOURCES


async def _collect_company(ticker: str, company_name: str) -> tuple[CompanyData, list[str]]:
    """Coleta dados de todas as fontes para uma empresa. Nunca lança exceção."""
    # Para futuros, usa o termo do ativo subjacente nas buscas textuais (ex: "ouro gold" em vez de "GLDQ26")
    underlying = futures_search_term(ticker)
    search_name = underlying if underlying else company_name
    results = await asyncio.gather(*[src.fetch(ticker, search_name) for src in ALL_SOURCES])

    quote = None
    news: list[dict] = []
    tavily_items: list[dict] = []
    reddit_items: list[dict] = []
    youtube_items: list[dict] = []
    trends_data: dict | None = None
    substack_items: list[dict] = []
    failed: list[str] = []

    for result in results:
        if not result.success:
            failed.append(result.source_name)
            continue
        match result.source_name:
            case "brapi":
                quote = result.data
            case "google_news":
                news = result.data.get("items", [])
            case "tavily":
                tavily_items = result.data.get("items", [])
            case "reddit":
                reddit_items = result.data.get("items", [])
            case "youtube":
                youtube_items = result.data.get("items", [])
            case "google_trends":
                trends_data = result.data
            case "substack":
                substack_items = result.data.get("items", [])

    return CompanyData(
        ticker=ticker,
        company_name=company_name,
        quote=quote,
        news=news,
        tavily_items=tavily_items,
        reddit_items=reddit_items,
        youtube_items=youtube_items,
        trends_data=trends_data,
        substack_items=substack_items,
        failed_sources=failed,
    ), failed


async def run_report(
    companies: list[dict],
    triggered_by: str = "scheduler",
    db: AsyncSession | None = None,
) -> dict:
    """
    Executa o pipeline completo.

    companies: lista de dicts com chaves 'ticker' e 'name'
    Retorna dict com status e métricas.
    """
    start = time.monotonic()
    logger.info("Iniciando relatório para %d empresa(s) [%s]", len(companies), triggered_by)

    all_failed_sources: list[str] = []
    summaries = []
    error_msg = ""

    for company in companies:
        ticker = company["ticker"].upper()
        name = company["name"]
        logger.info("Coletando dados: %s (%s)", name, ticker)

        company_data, failed = await _collect_company(ticker, name)
        all_failed_sources.extend(f"{ticker}:{src}" for src in failed)

        try:
            summary = await summarize_company(company_data)
            summaries.append(summary)
            logger.info("Resumo gerado: %s", ticker)
        except Exception as exc:
            logger.error("Falha ao gerar resumo para %s: %s", ticker, exc)
            all_failed_sources.append(f"{ticker}:summarizer")

    if not summaries:
        status = "error"
        error_msg = "Nenhum resumo gerado — todos os processos falharam."
        logger.error(error_msg)
    else:
        try:
            send_report(summaries, all_failed_sources, triggered_by)
            status = "partial" if all_failed_sources else "success"
        except Exception as exc:
            status = "error"
            error_msg = str(exc)
            logger.error("Falha ao enviar e-mail: %s", exc)

    duration = int(time.monotonic() - start)

    if db is not None:
        report = Report(
            status=status,
            triggered_by=triggered_by,
            tickers_requested=json.dumps([c["ticker"] for c in companies]),
            tickers_ok=json.dumps([s.ticker for s in summaries]),
            sources_failed=json.dumps(all_failed_sources) if all_failed_sources else None,
            email_subject=f"Relatório B3 — {datetime.now(timezone.utc).strftime('%d/%m %H:%M')}",
            email_sent_to=json.dumps(None),
            error_message=error_msg or None,
            duration_seconds=duration,
        )
        db.add(report)
        await db.commit()

    logger.info("Relatório concluído em %ds — status: %s", duration, status)
    return {
        "status": status,
        "summaries": len(summaries),
        "failed_sources": all_failed_sources,
        "duration_seconds": duration,
    }
