"""Fonte de Fatos Relevantes e Comunicados ao Mercado via CVM Open Data.

Baixa o CSV IPE (Informações Periódicas e Eventuais) de dados.cvm.gov.br,
filtra pelos documentos da empresa solicitada nos últimos 30 dias e retorna
títulos + links para os documentos oficiais.

O CSV fica em cache por 6 horas para evitar downloads repetidos.
"""

import csv
import io
import logging
import re
import time
from datetime import datetime, timedelta

import httpx

from .base import SourceBase

logger = logging.getLogger(__name__)

_IPE_URL = "https://dados.cvm.gov.br/dados/cia_aberta/doc/ipe/data/ipe_cia_aberta_{year}.csv"
_LOOKBACK_DAYS = 30
_MAX_ITEMS = 8
_CACHE_TTL = 6 * 3600  # 6 horas

# Categorias relevantes para análise de posição
_RELEVANT_CATEGORIES = {
    "Fato Relevante",
    "Comunicado ao Mercado",
    "Aviso aos Acionistas",
    "Acordo de Acionistas",
    "Aquisição de Ações",
    "Assembleia de Acionistas",
    "Fusão/Cisão/Incorporação",
    "Oferta Pública",
}

# Palavras genéricas que não ajudam no match de nome de empresa
_STOP_WORDS = {
    "SA", "DE", "DO", "DA", "DOS", "DAS", "E", "EM", "COM", "PARA",
    "HOLDING", "GRUPO", "BRASIL", "BRASILEIRO", "BRASILEIRA",
    "FUNDO", "CAPITAL", "INVESTIMENTOS", "PARTICIPAÇÕES",
}

# Cache de módulo: ano → (linhas, timestamp de expiração)
_ipe_cache: dict[int, tuple[list[dict], float]] = {}


def _normalize(text: str) -> str:
    """Remove pontuação e acentos para comparação."""
    text = text.upper()
    # Remove acentos básicos
    for src, dst in [("Ã","A"),("Â","A"),("Á","A"),("À","A"),("Ê","E"),("É","E"),
                     ("Í","I"),("Õ","O"),("Ô","O"),("Ó","O"),("Ú","U"),("Ç","C")]:
        text = text.replace(src, dst)
    return re.sub(r"[^\w\s]", " ", text)


def _match_company(cvm_name: str, company_name: str, ticker: str) -> bool:
    """Retorna True se o nome CVM parece ser a mesma empresa."""
    cvm_norm = _normalize(cvm_name)

    # 1. Ticker base (sem dígitos): BRAV3 → BRAV, PETR4 → PETR
    ticker_base = re.sub(r"\d", "", ticker.upper())
    if len(ticker_base) >= 4 and ticker_base in cvm_norm:
        return True

    # 2. Palavras significativas do nome da empresa
    name_norm = _normalize(company_name)
    significant = [w for w in name_norm.split() if len(w) > 3 and w not in _STOP_WORDS]
    if significant and all(w in cvm_norm for w in significant[:2]):
        return True

    return False


async def _get_ipe_rows(year: int) -> list[dict]:
    """Retorna linhas do CSV IPE para o ano, usando cache."""
    cached = _ipe_cache.get(year)
    if cached and cached[1] > time.time():
        return cached[0]

    url = _IPE_URL.format(year=year)
    logger.info("[cvm_ri] Baixando IPE CSV %d...", year)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    reader = csv.DictReader(
        io.StringIO(resp.content.decode("latin-1"), newline=""),
        delimiter=";",
    )
    rows = list(reader)
    _ipe_cache[year] = (rows, time.time() + _CACHE_TTL)
    logger.info("[cvm_ri] CSV %d carregado: %d documentos", year, len(rows))
    return rows


class CvmRiSource(SourceBase):
    name = "cvm_ri"

    async def _fetch(self, ticker: str, company_name: str) -> dict:
        now = datetime.now()
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)

        # Busca no ano corrente e, se estivermos nos primeiros 30 dias, também no anterior
        years = [now.year]
        if now.timetuple().tm_yday <= _LOOKBACK_DAYS:
            years.append(now.year - 1)

        all_rows: list[dict] = []
        for year in years:
            try:
                all_rows.extend(await _get_ipe_rows(year))
            except Exception as exc:
                logger.warning("[cvm_ri] Falha ao buscar CSV %d: %s", year, exc)

        if not all_rows:
            raise RuntimeError("Nenhum dado IPE disponível")

        items = []
        for row in all_rows:
            cvm_name = row.get("DENOM_CIA", "")
            if not _match_company(cvm_name, company_name, ticker):
                continue

            categ = row.get("CATEG_DOC", "").strip()
            if categ not in _RELEVANT_CATEGORIES:
                continue

            dt_str = (row.get("DT_RECEB") or row.get("DT_REFER") or "").strip()[:10]
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d")
            except ValueError:
                continue
            if dt < cutoff:
                continue

            subject = row.get("ASSUNTO", "").strip()
            link = row.get("LINK_DOC", "").strip()

            items.append({
                "date": dt_str,
                "category": categ,
                "subject": subject,
                "link": link,
                "company_cvm": cvm_name,
            })

        items.sort(key=lambda x: x["date"], reverse=True)
        return {"items": items[:_MAX_ITEMS]}
