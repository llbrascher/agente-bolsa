import httpx

from app.config import settings
from app.sources.base import SourceBase

BRAPI_BASE = "https://brapi.dev/api/quote"


class BrapiSource(SourceBase):
    """Cotação e variação diária via Brapi.dev."""

    name = "brapi"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        params = {"fundamental": "false"}
        if settings.brapi_token:
            params["token"] = settings.brapi_token

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BRAPI_BASE}/{ticker}", params=params)
            resp.raise_for_status()

        payload = resp.json()
        results = payload.get("results", [])
        if not results:
            raise ValueError(f"Ticker {ticker} não encontrado na Brapi")

        quote = results[0]
        return {
            "ticker": ticker,
            "price": quote.get("regularMarketPrice"),
            "change_pct": quote.get("regularMarketChangePercent"),
            "change_abs": quote.get("regularMarketChange"),
            "prev_close": quote.get("regularMarketPreviousClose"),
            "market_cap": quote.get("marketCap"),
            "short_name": quote.get("shortName") or company_name,
            "currency": quote.get("currency", "BRL"),
        }
