import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

SYSTEM_PROMPT = """Você é um analista sênior de uma gestora de ativos brasileira de primeira linha.
Sua função não é compilar notícias — é entregar inteligência acionável para um gestor de carteira.

Princípios:
• Interprete, não descreva: explique o que os dados SIGNIFICAM para o preço, não o que aconteceu.
• Causa e efeito: por que cada fator importa para este ativo agora?
• Posicionamento claro: tome um viés direcional fundamentado, sem omitir a lógica.
• Corte o ruído: se uma informação não move preço, não a inclua.
• Dados insuficientes: mencione em uma linha e complete com contexto macro/setorial relevante.

Para CONTRATOS FUTUROS (ticker no padrão letras+mês+ano):
  — Analise EXCLUSIVAMENTE o ativo subjacente (commodity, índice, câmbio).
  — Ignore completamente o contrato, ticker, vencimento, liquidez ou mecânica do derivativo.
  — Inclua: macro global, oferta/demanda do ativo, impacto cambial BRL/USD para o investidor.

Para AÇÕES:
  — Vá além do press release: contextualize no ciclo da empresa e do setor.
  — Identifique se o evento já está precificado ou se há assimetria residual."""

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
    cenario: str
    catalisadores: str
    riscos: str
    vies: str
    monitorar: str
    retail_summary: str
    sentiment: str = "neutro"  # "positivo", "neutro" ou "negativo"


def _build_user_message(data: CompanyData, quote_line: str) -> str:
    futures = _futures_info(data.ticker)

    if futures:
        header = (
            f"## {futures['underlying']}\n"
            f"Instrumento: contrato futuro {data.ticker} — analise APENAS o ativo subjacente."
        )
        context_instruction = (
            f'Contexto obrigatório para "{futures["underlying"]}":\n'
            f"• Macro global: Fed, inflação, dólar (DXY), apetite por risco\n"
            f"• Oferta e demanda física/financeira do ativo\n"
            f"• Impacto cambial BRL/USD para o investidor brasileiro\n"
            f"• NÃO mencione o contrato, o ticker {data.ticker}, vencimento ou liquidez"
        )
    else:
        header = f"## {data.company_name} ({data.ticker})"
        context_instruction = (
            f"Contexto para análise de {data.company_name} ({data.ticker}):\n"
            f"• Posicione os fatos no ciclo atual da empresa e do setor\n"
            f"• Identifique se eventos já estão precificados ou há assimetria residual\n"
            f"• Destaque catalisadores específicos com datas quando conhecidas"
        )

    retail_instruction = (
        '"retail_summary": "<1-2 parágrafos: tom do varejo, o que sinaliza sobre fluxo de '
        'pequeno investidor e se diverge ou confirma a análise institucional>"'
        if data.has_retail_data
        else '"retail_summary": ""'
    )

    return f"""{header}

### Cotação
{quote_line}

### Informações disponíveis

Notícias (Google News):
{_fmt_news(data.news)}

Busca aprofundada (Tavily):
{_fmt_tavily(data.tavily_items)}

Newsletters (Substack):
{_fmt_substack(data.substack_items)}

Reddit:
{_fmt_reddit(data.reddit_items)}

YouTube:
{_fmt_youtube(data.youtube_items)}

Google Trends:
{_fmt_trends(data.trends_data)}

---

{context_instruction}

Produza EXATAMENTE este JSON — sem markdown, sem texto fora do JSON:

{{
  "sentiment": "<positivo | neutro | negativo>",
  "cenario": "<2 parágrafos MAX: o que está acontecendo neste mercado e por que isso move o preço agora. Seja interpretativo, não descritivo.>",
  "catalisadores": "<bullets separados por \\n de catalisadores concretos de ALTA e de BAIXA com mecanismo de impacto. Ex: '↑ Fed dovish: juros reais negativos sustentam demanda por proteção\\n↓ DXY acima de 105: pressão sobre commodities em USD'>",
  "riscos": "<1 parágrafo: o principal risco que invalidaria o cenário atual e como identificá-lo>",
  "vies": "<1 parágrafo: direção analítica clara (alta/baixa/lateral), por quê, e o que confirmaria ou negaria essa tese>",
  "monitorar": "<lista de 3-5 itens específicos: eventos com datas, dados econômicos, níveis de preço ou indicadores a acompanhar>",
  {retail_instruction}
}}"""


async def summarize_company(data: CompanyData) -> SummaryResult:
    quote_line = _fmt_quote(data.quote or {})
    user_message = _build_user_message(data, quote_line)

    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[summarizer] JSON inválido para %s, usando fallback", data.ticker)
        parsed = {"cenario": raw, "catalisadores": "", "riscos": "", "vies": "", "monitorar": "", "retail_summary": ""}

    raw_sentiment = parsed.get("sentiment", "neutro").lower().strip()
    sentiment = raw_sentiment if raw_sentiment in {"positivo", "neutro", "negativo"} else "neutro"

    return SummaryResult(
        ticker=data.ticker,
        company_name=data.company_name,
        quote_line=quote_line,
        cenario=parsed.get("cenario", "Análise indisponível."),
        catalisadores=parsed.get("catalisadores", ""),
        riscos=parsed.get("riscos", ""),
        vies=parsed.get("vies", ""),
        monitorar=parsed.get("monitorar", ""),
        retail_summary=parsed.get("retail_summary", ""),
        sentiment=sentiment,
    )
