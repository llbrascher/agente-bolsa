"""Fonte de dados fundamentalistas via yfinance.

Para ações B3: busca TICKER.SA — retorna consenso de analistas, múltiplos,
saúde financeira e range 52 semanas.
Para futuros B3: busca o ativo subjacente no mercado global (GC=F para ouro,
^BVSP para Ibovespa, BRL=X para dólar, etc.).

yfinance gerencia autenticação (cookies + crumbs) automaticamente — sem necessidade
de API key. A chamada síncrona é executada em thread pool para não bloquear o event loop.
"""

import asyncio
import logging
import re

import yfinance as yf

from .base import SourceBase

logger = logging.getLogger(__name__)

_FUTURES_RE = re.compile(r"^([A-Z]{2,5})[FGHJKMNQUVXZ]\d{2}$")

# B3 futures prefix → Yahoo Finance ticker do ativo subjacente
_FUTURES_YF: dict[str, str | None] = {
    "GLD": "GC=F",       # Ouro (CME)
    "OZ":  "GC=F",
    "WIN": "^BVSP",      # Ibovespa
    "IND": "^BVSP",
    "WDO": "BRL=X",      # USD/BRL
    "DOL": "BRL=X",
    "BGI": None,          # Boi gordo — sem ticker no Yahoo
    "ICF": "KC=F",       # Café arábica (ICE)
    "CCM": "ZC=F",       # Milho (CBOT)
    "SJC": "ZS=F",       # Soja (CBOT)
    "ACF": "SB=F",       # Açúcar #11 (ICE)
    "ETH": None,          # Etanol — sem ticker no Yahoo
    "ISP": "^GSPC",      # S&P 500
    "EUR": "EURUSD=X",
    "GBP": "GBPUSD=X",
}


def _yf_ticker(b3_ticker: str) -> str | None:
    """Converte ticker B3 para ticker Yahoo Finance. Retorna None se sem cobertura."""
    m = _FUTURES_RE.match(b3_ticker.upper())
    if m:
        prefix = m.group(1)
        for length in (len(prefix), 3, 2):
            key = prefix[:length]
            if key in _FUTURES_YF:
                return _FUTURES_YF[key]
        return None
    return f"{b3_ticker.upper()}.SA"


def _sync_fetch_info(yf_ticker: str) -> dict:
    """Busca fundamentals via yfinance (síncrono — rodar em thread pool)."""
    ticker = yf.Ticker(yf_ticker)
    return ticker.info or {}


class YahooFinanceSource(SourceBase):
    name = "yahoo_finance"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        yf_tick = _yf_ticker(ticker)
        if not yf_tick:
            raise ValueError(f"Sem cobertura Yahoo Finance para {ticker}")

        info = await asyncio.to_thread(_sync_fetch_info, yf_tick)

        # Detecta se é ativo subjacente de futuro (não tem preço-alvo de analista)
        is_futures_underlying = bool(_FUTURES_RE.match(ticker.upper()))

        cur   = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        w52h  = info.get("fiftyTwoWeekHigh") or None
        w52l  = info.get("fiftyTwoWeekLow") or None
        # Yahoo Finance às vezes retorna 0.0 para ativos com dados incompletos
        if w52l == 0.0:
            w52l = None
        tgt   = info.get("targetMeanPrice")

        pct_from_52h = round((cur - w52h) / w52h * 100, 1) if (cur and w52h) else None
        pct_from_52l = round((cur - w52l) / w52l * 100, 1) if (cur and w52l) else None
        upside       = round((tgt - cur) / cur * 100, 1)   if (tgt and cur)  else None

        return {
            "yf_ticker":              yf_tick,
            "is_futures_underlying":  is_futures_underlying,
            "current_price":          cur,
            "target_mean":            tgt,
            "target_high":            info.get("targetHighPrice"),
            "target_low":             info.get("targetLowPrice"),
            "upside_pct":             upside,
            "n_analysts":             info.get("numberOfAnalystOpinions"),
            "recommendation":         info.get("recommendationKey", ""),
            "trailing_pe":            info.get("trailingPE"),
            "forward_pe":             info.get("forwardPE"),
            "ev_ebitda":              info.get("enterpriseToEbitda"),
            "price_to_book":          info.get("priceToBook"),
            "dividend_yield":         info.get("dividendYield"),
            "week_52_high":           w52h,
            "week_52_low":            w52l,
            "pct_from_52h":           pct_from_52h,
            "pct_from_52l":           pct_from_52l,
            "market_cap":             info.get("marketCap"),
            "debt_to_equity":         info.get("debtToEquity"),
            "ebitda":                 info.get("ebitda"),
            "ebitda_margins":         info.get("ebitdaMargins"),
            "return_on_equity":       info.get("returnOnEquity"),
            "revenue_ttm":            info.get("totalRevenue"),
        }
