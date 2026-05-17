import json
import logging
from dataclasses import dataclass, field

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

SYSTEM_PROMPT = """Você é um analista de mercado de capitais brasileiro especializado em ações da B3.
Seu papel é interpretar dados de cotação, notícias e sentimento de mercado, e produzir um resumo
claro, objetivo e útil para um investidor de varejo acompanhar suas posições.

Seja direto. Use linguagem acessível, mas precisa. Não invente informações que não estejam nos dados.
Se não houver dados suficientes em alguma seção, diga isso claramente."""


def _fmt_quote(quote: dict) -> str:
    price = quote.get("price")
    change = quote.get("change_pct")
    name = quote.get("short_name", quote.get("ticker", ""))
    ticker = quote.get("ticker", "")
    if price is None:
        return f"{ticker} — cotação indisponível"
    arrow = "▲" if (change or 0) >= 0 else "▼"
    sign = "+" if (change or 0) >= 0 else ""
    return f"{name} ({ticker}): R$ {price:.2f} {arrow} {sign}{change:.2f}%"


def _fmt_news(items: list[dict]) -> str:
    if not items:
        return "Nenhuma notícia encontrada."
    return "\n".join(f"{i+1}. {it['title']} [{it.get('source','')}]" for i, it in enumerate(items))


def _fmt_tavily(items: list[dict]) -> str:
    if not items:
        return "Nenhum resultado."
    lines = []
    for it in items:
        lines.append(f"• {it['title']}\n  {it['content'][:200]}")
    return "\n".join(lines)


def _fmt_reddit(items: list[dict]) -> str:
    if not items:
        return "Nenhum post encontrado."
    lines = []
    for it in items:
        snippet = it.get("text", "")[:150]
        text = f' — “{snippet}”' if snippet else ""
        lines.append(f"• [{it['subreddit']}] {it['title']} (↑{it['score']}){text}")
    return "\n".join(lines)


def _fmt_youtube(items: list[dict]) -> str:
    if not items:
        return "Nenhum vídeo encontrado."
    lines = []
    for it in items:
        snippet = it.get("transcript", "")[:200]
        transcript = f'\n  Trecho: "{snippet}"' if snippet else ""
        lines.append(f"• {it['title']} [{it['channel']}]{transcript}")
    return "\n".join(lines)


def _fmt_trends(data: dict | None) -> str:
    if not data or not data.get("trend_available"):
        return "Dados de tendência indisponíveis."
    lines = []
    for kw, avg in data.get("avg_interest", {}).items():
        direction = data.get("direction", {}).get(kw, "estável")
        lines.append(f'• "{kw}": interesse médio {avg}/100, tendência {direction}')
    return "\n".join(lines) if lines else "Sem dados."


def _fmt_substack(items: list[dict]) -> str:
    if not items:
        return "Nenhuma menção em newsletters monitoradas."
    lines = []
    for it in items:
        snippet = it.get("summary", "")[:200]
        text = f'\n  "{snippet}"' if snippet else ""
        lines.append(f"• {it['title']} [{it['source']}]{text}")
    return "\n".join(lines)


@dataclass
class CompanyData:
    ticker: str
    company_name: str
    quote: dict | None = None
    news: list[dict] = field(default_factory=list)
    tavily_items: list[dict] = field(default_factory=list)
    reddit_items: list[dict] = field(default_factory=list)
    youtube_items: list[dict] = field(default_factory=list)
    trends_data: dict | None = None
    substack_items: list[dict] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)

    @property
    def has_retail_data(self) -> bool:
        return bool(self.reddit_items or self.youtube_items or self.trends_data)


@dataclass
class SummaryResult:
    ticker: str
    company_name: str
    quote_line: str
    institutional_summary: str
    retail_summary: str
    full_text: str
    sentiment: str = "neutro"  # "positivo", "neutro" ou "negativo"


async def summarize_company(data: CompanyData) -> SummaryResult:
    quote_line = _fmt_quote(data.quote or {})

    has_retail = data.has_retail_data
    retail_instruction = (
        'Preencha "retail_summary" com 1-3 parágrafos sobre o sentimento do varejo '
        "(Reddit, YouTube, Google Trends). Destaque: tom predominante (otimista/pessimista), "
        "temas recorrentes, volume de discussão."
        if has_retail
        else '"retail_summary": ""'
    )

    user_message = f"""## {data.company_name} ({data.ticker})

### Cotação
{quote_line}

### Notícias institucionais (Google News)
{_fmt_news(data.news)}

### Busca aprofundada (Tavily)
{_fmt_tavily(data.tavily_items)}

### Newsletters financeiras (Substack)
{_fmt_substack(data.substack_items)}

### Reddit (comunidades de investimento BR)
{_fmt_reddit(data.reddit_items)}

### YouTube (análises recentes)
{_fmt_youtube(data.youtube_items)}

### Google Trends (últimos 7 dias, Brasil)
{_fmt_trends(data.trends_data)}

---

Gere um resumo seguindo EXATAMENTE esta estrutura JSON:

{{
  "sentiment": "<uma palavra: positivo, neutro ou negativo — avaliação geral do conjunto de notícias para o acionista>",
  "institutional_summary": "<2-4 parágrafos analisando notícias e busca Tavily. Destaque: o que move o preço, resultados, fatos relevantes, riscos.>",
  "{retail_instruction}"
}}

Responda APENAS com o JSON, sem markdown, sem texto fora do JSON."""

    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[summarizer] JSON inválido para %s, usando texto bruto", data.ticker)
        parsed = {"institutional_summary": raw, "retail_summary": ""}

    institutional = parsed.get("institutional_summary", "Resumo indisponível.")
    retail = parsed.get("retail_summary", "")
    raw_sentiment = parsed.get("sentiment", "neutro").lower().strip()
    sentiment = raw_sentiment if raw_sentiment in {"positivo", "neutro", "negativo"} else "neutro"

    full_text = f"{quote_line}\n\n{institutional}"
    if retail:
        full_text += f"\n\n**Sentimento do Varejo**\n{retail}"

    return SummaryResult(
        ticker=data.ticker,
        company_name=data.company_name,
        quote_line=quote_line,
        institutional_summary=institutional,
        retail_summary=retail,
        full_text=full_text,
        sentiment=sentiment,
    )
