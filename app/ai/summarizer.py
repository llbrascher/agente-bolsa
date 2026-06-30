import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

SYSTEM_PROMPT = """Você é um analista de mercado de capitais brasileiro especializado em ações da B3
e em commodities e derivativos negociados na B3.
Seu papel é interpretar dados de cotação, notícias e sentimento de mercado, e produzir um resumo
claro, objetivo e útil para um investidor de varejo acompanhar suas posições.

Para contratos futuros: analise o ATIVO SUBJACENTE (commodity, índice ou câmbio), não o contrato
em si. Inclua perspectivas de oferta/demanda, fatores macroeconômicos e impacto cambial quando
relevante para o investidor brasileiro.

Seja direto. Use linguagem acessível, mas precisa. Não invente informações que não estejam nos dados.
Se não houver dados suficientes em alguma seção, diga isso claramente."""

# Padrão de ticker futuro da B3: prefixo de letras + código de mês + 2 dígitos do ano
_FUTURES_RE = re.compile(r'^([A-Z]{2,5})([FGHJKMNQUVXZ])(\d{2})$')

# prefixo → (termo de busca, nome para exibição)
_FUTURES_MAP: dict[str, tuple[str, str]] = {
    'GLD':  ('ouro gold mercados',          'Ouro (Gold)'),
    'OZ':   ('ouro B3',                     'Ouro à vista B3'),
    'WIN':  ('Ibovespa índice futuro',       'Ibovespa Mini (WIN)'),
    'IND':  ('Ibovespa índice futuro',       'Ibovespa Índice (IND)'),
    'WDO':  ('dólar real câmbio futuro',     'Mini Dólar (WDO)'),
    'DOL':  ('dólar real câmbio futuro',     'Dólar Futuro (DOL)'),
    'BGI':  ('boi gordo pecuária futuro',    'Boi Gordo (BGI)'),
    'ICF':  ('café arábica commodity',       'Café Arábica (ICF)'),
    'CCM':  ('milho commodity grãos',        'Milho (CCM)'),
    'SJC':  ('soja commodity grãos',         'Soja (SJC)'),
    'ACF':  ('açúcar commodity',             'Açúcar Cristal (ACF)'),
    'ETH':  ('etanol combustível',           'Etanol (ETH)'),
    'ISP':  ('S&P 500 bolsa americana',      'S&P 500 (ISP)'),
    'EUR':  ('euro câmbio moeda',            'Euro (EUR)'),
    'GBP':  ('libra câmbio moeda',           'Libra Esterlina (GBP)'),
}

_FUTURES_MONTHS = {
    'F': 'janeiro', 'G': 'fevereiro', 'H': 'março',    'J': 'abril',
    'K': 'maio',    'M': 'junho',     'N': 'julho',     'Q': 'agosto',
    'U': 'setembro','V': 'outubro',   'X': 'novembro',  'Z': 'dezembro',
}


def _futures_info(ticker: str) -> dict | None:
    """Retorna info do contrato futuro ou None se não for futuro."""
    m = _FUTURES_RE.match(ticker.upper())
    if not m:
        return None
    prefix, month_code, year = m.group(1), m.group(2), m.group(3)
    search_term, display_name = None, None
    for length in (len(prefix), 3, 2):
        entry = _FUTURES_MAP.get(prefix[:length])
        if entry:
            search_term, display_name = entry
            break
    if not search_term:
        search_term = prefix.lower()
        display_name = f"futuro {prefix}"
    return {
        'underlying': display_name,
        'search_term': search_term,
        'month': _FUTURES_MONTHS.get(month_code, month_code),
        'year': f"20{year}",
        'expiry': f"{_FUTURES_MONTHS.get(month_code, month_code)}/20{year}",
    }


def futures_search_term(ticker: str) -> str | None:
    """Retorna o termo de busca do ativo subjacente, ou None se não for futuro."""
    info = _futures_info(ticker)
    return info['search_term'] if info else None


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

    futures = _futures_info(data.ticker)
    if futures:
        futures_block = (
            f"\n### Contrato futuro\n"
            f"Ticker: {data.ticker} | Ativo subjacente: **{futures['underlying']}** "
            f"| Vencimento: {futures['expiry']}\n"
            f"Analise o ATIVO SUBJACENTE ({futures['underlying']}), não o contrato em si. "
            f"Inclua: dinâmica de oferta/demanda, fatores macro (Fed, inflação, geopolítica), "
            f"impacto cambial BRL/USD para o investidor brasileiro, e perspectiva de preço "
            f"para o vencimento em {futures['expiry']}."
        )
    else:
        futures_block = ""

    user_message = f"""## {data.company_name} ({data.ticker}){futures_block}

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
