import asyncio

from app.sources.base import SourceBase


class GoogleTrendsSource(SourceBase):
    """Interesse de busca via Google Trends (pytrends — não-oficial, fragile)."""

    name = "google_trends"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        from pytrends.request import TrendReq  # import tardio

        def _get() -> dict:
            pt = TrendReq(hl="pt-BR", tz=-180, timeout=(5, 15))
            kw_list = [ticker, company_name]
            pt.build_payload(kw_list, timeframe="now 7-d", geo="BR")
            interest = pt.interest_over_time()

            if interest.empty:
                return {"ticker": ticker, "keywords": kw_list, "trend_available": False, "points": []}

            # Últimos 7 dias: média de interesse relativo (0-100)
            avg = {}
            for kw in kw_list:
                if kw in interest.columns:
                    avg[kw] = round(float(interest[kw].mean()), 1)

            # Direção: subindo ou caindo (compara primeira e última metade)
            mid = len(interest) // 2
            direction = {}
            for kw in kw_list:
                if kw in interest.columns:
                    first_half = interest[kw].iloc[:mid].mean()
                    second_half = interest[kw].iloc[mid:].mean()
                    direction[kw] = "subindo" if second_half > first_half else "caindo"

            return {
                "ticker": ticker,
                "keywords": kw_list,
                "trend_available": True,
                "avg_interest": avg,
                "direction": direction,
            }

        return await asyncio.to_thread(_get)
