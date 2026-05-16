import httpx

from app.sources.base import SourceBase

SUBREDDITS = ["investimentos", "BrasilFinancas", "acoes"]
MAX_POSTS = 10
HEADERS = {"User-Agent": "agente-bolsa/1.0"}


class RedditSource(SourceBase):
    """Posts recentes sobre o ticker em subreddits BR via API JSON pública."""

    name = "reddit"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        query = f"{ticker} OR {company_name}"
        items: list[dict] = []

        async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
            for sub in SUBREDDITS:
                try:
                    resp = await client.get(
                        f"https://www.reddit.com/r/{sub}/search.json",
                        params={"q": query, "sort": "new", "t": "week", "limit": 5, "restrict_sr": "true"},
                    )
                    resp.raise_for_status()
                    for post in resp.json().get("data", {}).get("children", []):
                        d = post["data"]
                        items.append({
                            "subreddit": sub,
                            "title": d["title"],
                            "score": d["score"],
                            "url": f"https://reddit.com{d['permalink']}",
                            "text": d.get("selftext", "")[:300],
                            "num_comments": d["num_comments"],
                        })
                except Exception:
                    pass

        items.sort(key=lambda x: x["score"], reverse=True)
        return {"ticker": ticker, "query": query, "subreddits": SUBREDDITS, "items": items[:MAX_POSTS]}
