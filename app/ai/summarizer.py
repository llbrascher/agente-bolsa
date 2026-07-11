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


def _escape_json_newlines(s: str) -> str:
    """Escapa newlines literais dentro de strings JSON — O(n), trata aspas escapadas."""
    out, in_str = [], False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '\\' and in_str:          # sequência de escape — passa os dois chars
            out.append(ch)
            i += 1
            if i < len(s):
                out.append(s[i])
        elif ch == '"':
            in_str = not in_str
            out.append(ch)
        elif in_str and ch == '\n':
            out.append('\\n')
        elif in_str and ch == '\r':
            pass                            # descarta \r dentro de strings
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _fmt_fundamentals(fd: dict | None, is_futures: bool = False) -> str:
    if not fd:
        return "Indisponível."

    lines = []
    cur = fd.get("current_price")
    w52h = fd.get("week_52_high")
    w52l = fd.get("week_52_low")

    if is_futures:
        # Para futuros: preço do ativo subjacente no mercado global
        yf = fd.get("yf_ticker", "")
        if cur:
            lines.append(f"Preço atual ({yf}): {cur:,.2f}")
        if w52h and w52l:
            lines.append(f"Range 52 semanas: {w52l:,.2f} – {w52h:,.2f}")
            if cur:
                pct_h = fd.get("pct_from_52h")
                pct_l = fd.get("pct_from_52l")
                if pct_h is not None and pct_l is not None:
                    lines.append(
                        f"Posição: {pct_h:+.1f}% do topo de 52 sem. | {pct_l:+.1f}% do fundo"
                    )
        return "\n".join(lines) if lines else "Indisponível."

    # Para ações: dados fundamentalistas completos
    rec_map = {
        "strong_buy": "COMPRA FORTE", "buy": "COMPRA", "hold": "NEUTRO",
        "sell": "VENDA", "strong_sell": "VENDA FORTE",
    }
    rec = rec_map.get(fd.get("recommendation", ""), "")
    n   = fd.get("n_analysts")
    tgt = fd.get("target_mean")
    upside = fd.get("upside_pct")

    if tgt and upside is not None:
        analyst_line = f"Consenso ({n} analistas): {rec} | Preço-alvo médio: {tgt:.2f}"
        if fd.get("target_low") and fd.get("target_high"):
            analyst_line += f" (range: {fd['target_low']:.2f} – {fd['target_high']:.2f})"
        analyst_line += f" → {upside:+.1f}% vs. preço atual"
        lines.append(analyst_line)

    mult = []
    if fd.get("trailing_pe"):
        mult.append(f"P/L: {fd['trailing_pe']:.1f}x")
    if fd.get("forward_pe"):
        mult.append(f"P/L fwd: {fd['forward_pe']:.1f}x")
    if fd.get("ev_ebitda"):
        mult.append(f"EV/EBITDA: {fd['ev_ebitda']:.1f}x")
    if fd.get("price_to_book"):
        mult.append(f"P/VP: {fd['price_to_book']:.1f}x")
    if mult:
        lines.append("Múltiplos: " + " | ".join(mult))

    extras = []
    if fd.get("dividend_yield"):
        extras.append(f"DY: {fd['dividend_yield']*100:.1f}%")
    if fd.get("ebitda_margins"):
        extras.append(f"Mg. EBITDA: {fd['ebitda_margins']*100:.1f}%")
    if fd.get("debt_to_equity"):
        extras.append(f"Dívida/PL: {fd['debt_to_equity']:.1f}x")
    if fd.get("return_on_equity"):
        extras.append(f"ROE: {fd['return_on_equity']*100:.1f}%")
    if extras:
        lines.append(" | ".join(extras))

    if w52h and w52l:
        range_line = f"Range 52 sem.: {w52l:.2f} – {w52h:.2f}"
        pct_h = fd.get("pct_from_52h")
        pct_l = fd.get("pct_from_52l")
        if pct_h is not None and pct_l is not None:
            range_line += f" | atual: {pct_h:+.1f}% do topo, {pct_l:+.1f}% do fundo"
        lines.append(range_line)

    return "\n".join(lines) if lines else "Indisponível."


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
    fundamental_data: dict | None = None
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
    is_futures = futures is not None

    # --- cabeçalho e instruções específicas por tipo de ativo ---
    if is_futures:
        underlying = futures["underlying"]
        header = f"## {underlying}"
        etapa1_instrucao = (
            f"Com base nos dados de mercado acima, avalie onde o {underlying} está em termos históricos "
            f"(% do range 52 semanas) e o que isso implica para o risco/retorno da posição atual."
        )
        etapa2_instrucao = (
            f"Identifique o driver macro primário do {underlying} agora "
            f"(política monetária, inflação, DXY, oferta/demanda física, geopolítica) e sua direção. "
            f"DESCARTE qualquer dado sobre ETFs, produtos B3 ou volumes de bolsa — irrelevante."
        )
        etapa3_instrucao = (
            f"Com base no range 52 semanas e no preço atual, classifique: tendência (alta/baixa/lateral), "
            f"nível relativo (próximo do topo / meio do range / próximo do fundo) e o que isso implica "
            f"para o timing de entrada/saída."
        )
    else:
        header = f"## {data.company_name} ({data.ticker})"
        etapa1_instrucao = (
            f"Com base no preço-alvo de consenso e nos múltiplos acima, calcule o upside/downside implícito "
            f"e diga se o papel está caro, justo ou barato vs. histórico e peers. "
            f"Mencione a entrega operacional mais recente que explica esse múltiplo."
        )
        etapa2_instrucao = (
            f"Identifique o fator setorial que mais move {data.company_name} "
            f"(ex: Brent para petroleiras, minério para mineradoras, Selic para bancos) e diga qual a "
            f"direção atual desse fator e o que ela implica para o resultado da empresa."
        )
        etapa3_instrucao = (
            f"Com base no range 52 semanas e no preço atual de {quote_line}, diga se o papel está em "
            f"tendência de alta ou baixa, e qual o suporte/resistência mais relevante no nível atual."
        )

    retail_instruction = (
        '"retail_summary": "<1-2 parágrafos: tom do varejo, diverge ou confirma a análise fundamentalista? '
        'Sinaliza fluxo de mãos fracas ou forte convicção?>"'
        if data.has_retail_data
        else '"retail_summary": ""'
    )

    fd_formatted = _fmt_fundamentals(data.fundamental_data, is_futures=is_futures)

    newsflow = f"""Notícias (Google News):
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
{_fmt_trends(data.trends_data)}"""

    return f"""{header}

### Cotação atual
{quote_line}

### Dados de mercado (Yahoo Finance)
{fd_formatted}

### Newsflow recente
{newsflow}

---
Raciocine em sequência:

ETAPA 1 — VALUATION / POSICIONAMENTO HISTÓRICO
{etapa1_instrucao}

ETAPA 2 — DRIVER DE SETOR / MACRO
{etapa2_instrucao}

ETAPA 3 — TÉCNICA RÁPIDA
{etapa3_instrucao}

ETAPA 4 — NEWSFLOW COMO SINAL
O newsflow acima confirma, contradiz ou é ruído em relação à tese fundamentalista?
Só mencione o que altera a análise — ignore repetições e ruídos.

Produza EXATAMENTE este JSON — sem markdown, sem texto fora do JSON:

{{
  "sentiment": "<positivo | neutro | negativo>",
  "cenario": "<2 parágrafos MAX: síntese das etapas 1 e 2 — valuation + driver setorial/macro. Mecanismo, não evento.>",
  "catalisadores": "<bullets, um por linha: '↑ [evento]: [mecanismo de impacto no preço]' e '↓ [evento]: [mecanismo]'>",
  "riscos": "<1 parágrafo: o que invalida o cenário, o sinal que antecipa e o impacto esperado>",
  "vies": "<ALTA / BAIXA / LATERAL — lógica em 2 frases integrando valuation + técnica + newsflow. O que confirmaria e o que negaria.>",
  "monitorar": "<3-5 itens concretos: próximos dados econômicos com datas, níveis de preço críticos, eventos corporativos>",
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

    # Remove markdown code fences que o Claude adiciona apesar da instrução
    if raw.startswith("```"):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # LLMs às vezes inserem newlines literais dentro de strings JSON (inválido).
        # Percorremos o texto e escapamos newlines dentro de strings.
        parsed = None
        try:
            parsed = json.loads(_escape_json_newlines(raw))
        except json.JSONDecodeError:
            logger.warning("[summarizer] JSON inválido para %s, usando fallback", data.ticker)
        if parsed is None:
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
