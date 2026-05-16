import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SourceResult:
    source_name: str
    ticker: str
    success: bool
    data: dict = field(default_factory=dict)
    error: str = ""


class SourceBase(ABC):
    """Toda fonte de dados herda daqui.

    Nunca propaga exceção — retorna SourceResult com success=False em caso de falha,
    para que o pipeline continue com as demais fontes.
    """

    name: str = "base"

    async def fetch(self, ticker: str, company_name: str) -> SourceResult:
        try:
            data = await self._fetch(ticker, company_name)
            return SourceResult(source_name=self.name, ticker=ticker, success=True, data=data)
        except Exception as exc:
            logger.warning("[%s] Falha ao buscar %s: %s", self.name, ticker, exc)
            return SourceResult(
                source_name=self.name, ticker=ticker, success=False, error=str(exc)
            )

    @abstractmethod
    async def _fetch(self, ticker: str, company_name: str) -> dict:
        """Retorna dict com os dados coletados. Pode lançar exceção livremente."""
