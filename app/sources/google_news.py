import asyncio
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

from app.sources.base import SourceBase

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
MAX_ITEMS = 8
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AgenteBolsa/0.1)"}


class GoogleNewsSource(SourceBase):
    """Notícias recentes via Google News RSS (sem chave de API)."""

    name = "google_news"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        query = quote_plus(f"{company_name} {ticker} ação bolsa")
        url = f"{GOOGLE_NEWS_RSS}?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"

        # httpx busca o XML com User-Agent; feedparser só parseia o texto
        async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        feed = await asyncio.to_thread(feedparser.parse, resp.text)

        items = []
        for entry in feed.entries[:MAX_ITEMS]:
            published = entry.get("published", "")
            items.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": published,
                "source": entry.get("source", {}).get("title", ""),
            })

        return {
            "ticker": ticker,
            "query": query,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }
