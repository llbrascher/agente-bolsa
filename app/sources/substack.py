import feedparser
import httpx
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.sources.base import SourceBase

LOOKBACK_DAYS = 7
MAX_ITEMS = 5
HEADERS = {"User-Agent": "agente-bolsa/1.0"}


class SubstackSource(SourceBase):
    """Newsletters do Substack: filtra por menção ao ticker nos últimos 7 dias."""

    name = "substack"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        feeds = settings.substack_feed_list
        if not feeds:
            raise ValueError("SUBSTACK_FEEDS não configurado")

        cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
        items: list[dict] = []

        async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
            for feed_url in feeds:
                try:
                    resp = await client.get(feed_url)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                    feed_title = feed.feed.get("title", feed_url)

                    for entry in feed.entries:
                        text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
                        if ticker.lower() not in text and company_name.lower() not in text:
                            continue

                        published = entry.get("published_parsed")
                        if published:
                            pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                            if pub_dt < cutoff:
                                continue

                        items.append({
                            "title": entry.get("title", ""),
                            "url": entry.get("link", ""),
                            "source": feed_title,
                            "published": entry.get("published", ""),
                            "summary": entry.get("summary", "")[:250],
                        })
                except Exception:
                    pass

        items.sort(key=lambda x: x.get("published", ""), reverse=True)
        return {"ticker": ticker, "items": items[:MAX_ITEMS]}
