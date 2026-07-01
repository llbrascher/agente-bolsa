import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

SYSTEM_PROMPT = """Você é um analista sênior de uma gestora de ativos brasileira de primeira linha.
Sua função é transformar dados brutos em inteligência acionável — não resumir o que outros já disseram.

═══ REGRAS ABSOLUTAS ═══

PROIBIDO:
• Citar ou parafrasear analistas, bancos ou casas de análise externas por nome (XP, Genial, Goldman, etc.)
• Usar as frases "analistas destacam", "segundo especialistas", "de acordo com", "mercado acredita"
• Descrever eventos sem extrair implicação para o preço ("A empresa reportou X" sem concluir nada)
• Para FUTUROS: qualquer menção a ETFs, produtos B3, volumes de bolsa, liquidez do contrato ou ticker

OBRIGATÓRIO:
• Toda conclusão deve derivar dos dados fornecidos — raciocine, não relay
• Para cada fato relevante: explicite o mecanismo de transmissão ao preço ("X implica Y porque Z")
• Catalisadores: nomear evento + mecanismo de impacto + magnitude esperada quando possível
• Viés: posicionamento claro (alta/baixa/lateral) com a lógica completa — sem hedge vazio
• Dados insuficientes: assuma contexto macro/setorial de conhecimento próprio, não paralise

Para CONTRATOS FUTUROS:
  Analise EXCLUSIVAMENTE o ativo subjacente no mercado global.
  O leitor sabe que opera um futuro — não mencione o instrumento, a B3 ou qualquer produto local.
  Framework obrigatório: (1) drivers macro globais, (2) oferta/demanda física, (3) posicionamento de
  mercado/especulativo, (4) impacto do câmbio BRL/USD para o investidor brasileiro.

Para AÇÕES:
  Parta dos fatos, construa a tese. Quantifique impacto quando possível.
  Identifique se eventos já estão no preço ou se há assimetria não capturada pelo mercado."""

# Padrão de ticker futuro da B3: prefixo de letras + código de mês + 2 dígitos do ano
_FUTURES_RE = re.compile(r'^([A-Z]{2,5})([FGHJKMNQUVXZ])(\d{2})$')

# prefixo → (termo de busca global, nome para exibição)
# Termos de busca focados em fundamentos globais do ativo, NÃO em produtos/volumes da B3
_FUTURES_MAP: dict[str, tuple[str, str]] = {
    'GLD':  ('gold price inflation Fed central banks demand XAU',  'Ouro (Gold)'),
    'OZ':   ('gold price inflation Fed central banks demand XAU',  'Ouro (Gold)'),
    'WIN':  ('Ibovespa resultados empresas Brasil macro fiscal',    'Ibovespa'),
    'IND':  ('Ibovespa resultados empresas Brasil macro fiscal',    'Ibovespa'),
    'WDO':  ('dólar real câmbio Brasil Fed juros DXY',             'Dólar/Real (USD/BRL)'),
    'DOL':  ('dólar real câmbio Brasil Fed juros DXY',             'Dólar/Real (USD/BRL)'),
    'BGI':  ('boi gordo preço arroba pecuária exportação carne',   'Boi Gordo'),
    'ICF':  ('café arábica preço internacional Colombia safra',     'Café Arábica'),
    'CCM':  ('milho preço grãos safra Estados Unidos China demanda','Milho'),
    'SJC':  ('soja preço internacional safra Brasil Estados Unidos','Soja'),
    'ACF':  ('açúcar preço internacional safra Brasil India',       'Açúcar Cristal'),
    'ETH':  ('etanol preço combustível cana Brasil ANP',            'Etanol'),
    'ISP':  ('S&P 500 earnings Fed economy US macro',              'S&P 500'),
    'EUR':  ('euro dólar ECB inflação Europa câmbio',               'Euro/Dólar'),
    'GBP':  ('libra dólar Bank of England inflação Reino Unido',    'Libra Esterlina'),
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
        header = f"## {futures['underlying']} — análise de ativo subjacente"
        context_instruction = (
            f"ATIVO A ANALISAR: {futures['underlying']} no mercado global.\n\n"
            f"DESCARTE qualquer dado abaixo que se refira a: ETFs, produtos de bolsa, volumes "
            f"negociados em bolsa, contratos listados, liquidez de instrumento ou B3. "
            f"Esses dados são irrelevantes — use apenas informações sobre o ativo no mercado global.\n\n"
            f"Framework de análise obrigatório:\n"
            f"1. Drivers macro globais: política monetária (Fed, bancos centrais), inflação, DXY, apetite por risco\n"
            f"2. Fundamentos físicos: oferta, demanda, estoques, sazonalidade\n"
            f"3. Posicionamento especulativo: COT report, sentimento de mercado global\n"
            f"4. Ângulo brasileiro: impacto cambial BRL/USD, como o real amplifica ou atenua o movimento"
        )
    else:
        header = f"## {data.company_name} ({data.ticker})"
        context_instruction = (
            f"ATIVO A ANALISAR: {data.company_name} ({data.ticker}).\n\n"
            f"Parta dos fatos, construa a tese. Para cada dado relevante, explicite o mecanismo de "
            f"transmissão ao preço. Não repita o que analistas de terceiros disseram — raciocine "
            f"diretamente sobre os dados.\n\n"
            f"Identifique:\n"
            f"1. O fator dominante que está movendo (ou vai mover) o papel\n"
            f"2. Se eventos recentes já estão precificados ou há assimetria residual\n"
            f"3. O próximo catalisador concreto com data quando conhecida"
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

Produza EXATAMENTE este JSON — sem markdown, sem texto fora do JSON.
Lembre: raciocine sobre os dados, nunca cite analistas externos pelo nome; cada campo deve ter conclusão própria.

{{
  "sentiment": "<positivo | neutro | negativo — balanço geral de risco/retorno>",
  "cenario": "<2 parágrafos: qual a narrativa dominante AGORA e por que ela sustenta ou pressiona o preço. Explicite o mecanismo, não apenas o evento.>",
  "catalisadores": "<um bullet por linha: '↑ [evento]: [mecanismo de impacto]' para alta e '↓ [evento]: [mecanismo]' para baixa. Mínimo 2 de cada quando existirem.>",
  "riscos": "<1 parágrafo: o que invalidaria o cenário, qual o sinal antecipado e impacto esperado no preço>",
  "vies": "<ALTA / BAIXA / LATERAL + lógica em 2-3 frases. O que confirmaria e o que negaria a tese.>",
  "monitorar": "<3-5 itens acionáveis: datas de eventos, níveis de preço, dados econômicos ou indicadores concretos>",
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

    # Remove markdown code fences that Claude sometimes adds despite instructions
    if raw.startswith("```"):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        raw = raw.strip()

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
