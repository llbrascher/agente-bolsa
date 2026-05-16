import asyncio

import httpx

from app.config import settings
from app.sources.base import SourceBase

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
MAX_VIDEOS = 4
MAX_TRANSCRIPT_CHARS = 800


class YouTubeSource(SourceBase):
    """Vídeos recentes + trecho de transcrição via YouTube Data API."""

    name = "youtube"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        if not settings.youtube_api_key:
            raise ValueError("YOUTUBE_API_KEY não configurada")

        query = f"{company_name} {ticker} ações análise"
        videos = await self._search_videos(query)
        videos_with_transcript = await asyncio.gather(
            *[self._add_transcript(v) for v in videos],
            return_exceptions=True,
        )

        items = [v for v in videos_with_transcript if isinstance(v, dict)]
        return {"ticker": ticker, "query": query, "items": items}

    async def _search_videos(self, query: str) -> list[dict]:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": MAX_VIDEOS,
            "order": "date",
            "relevanceLanguage": "pt",
            "key": settings.youtube_api_key,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(YOUTUBE_SEARCH_URL, params=params)
            resp.raise_for_status()

        data = resp.json()
        return [
            {
                "video_id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "published": item["snippet"]["publishedAt"],
                "url": f"https://youtube.com/watch?v={item['id']['videoId']}",
                "transcript": "",
            }
            for item in data.get("items", [])
        ]

    async def _add_transcript(self, video: dict) -> dict:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # import tardio

            def _get() -> str:
                transcript = YouTubeTranscriptApi.get_transcript(
                    video["video_id"], languages=["pt", "pt-BR", "en"]
                )
                text = " ".join(seg["text"] for seg in transcript)
                return text[:MAX_TRANSCRIPT_CHARS]

            video["transcript"] = await asyncio.to_thread(_get)
        except Exception:
            video["transcript"] = ""  # sem legenda — continua com título apenas
        return video
