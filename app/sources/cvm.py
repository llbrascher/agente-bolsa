import httpx
from datetime import datetime, timedelta, timezone

from app.sources.base import SourceBase

CVM_SEARCH_URL = "https://efts.cvm.gov.br/EFTS/2/search"
FATO_RELEVANTE_KEYWORDS = {"fato relevante", "fato_relevante"}


class CVMSource(SourceBase):
    """Fatos relevantes publicados na CVM nas últimas 24h via EFTS (API pública)."""

    name = "cvm"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)

        params = {
            "q": ticker,
            "dateRange": "custom",
            "startDateOfReceipt": since.strftime("%Y-%m-%d"),
            "endDateOfReceipt": now.strftime("%Y-%m-%d"),
            "category": "IPE",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(CVM_SEARCH_URL, params=params)
            resp.raise_for_status()

        hits = resp.json().get("hits", {}).get("hits", [])

        items = []
        for hit in hits:
            src = hit.get("_source", {})
            tipo = src.get("tipoDocumento", "").lower()
            if not any(kw in tipo for kw in FATO_RELEVANTE_KEYWORDS):
                continue
            items.append({
                "title": src.get("assunto") or src.get("descricao") or "Fato Relevante",
                "date": src.get("dataEntrega", ""),
                "type": src.get("tipoDocumento", ""),
                "url": src.get("linkDoc", ""),
                "company": src.get("nomeEmissor", ""),
            })

        return {"ticker": ticker, "items": items, "period_hours": 24}
