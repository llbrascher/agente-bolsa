from app.config import settings
from app.sources.base import SourceBase

MAX_RESULTS = 5


class TavilySource(SourceBase):
    """Busca de notícias via Tavily Search API."""

    name = "tavily"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        if not settings.tavily_api_key:
            raise ValueError("TAVILY_API_KEY não configurada")

        from tavily import TavilyClient  # import tardio — chave pode não existir

        client = TavilyClient(api_key=settings.tavily_api_key)
        query = f"{company_name} {ticker} ações B3 notícias"

        # search() é síncrono — wrapping em asyncio.to_thread para não bloquear
        import asyncio
        response = await asyncio.to_thread(
            client.search,
            query=query,
            search_depth="basic",
            max_results=MAX_RESULTS,
            include_answer=False,
        )

        items = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:500],  # trunca para não explodir o prompt
                "score": r.get("score", 0),
            }
            for r in response.get("results", [])
        ]

        return {"ticker": ticker, "query": query, "items": items}
