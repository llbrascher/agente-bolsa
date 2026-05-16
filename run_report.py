"""Script de disparo manual — útil para testar sem precisar da interface web.

Uso:
    python run_report.py
    python run_report.py PETR4 VALE3 ITUB4
"""

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Empresas padrão para teste — edite à vontade
DEFAULT_COMPANIES = [
    {"ticker": "PETR4", "name": "Petrobras"},
    {"ticker": "VALE3", "name": "Vale"},
]


async def main():
    from app.tasks.report_task import run_report

    if len(sys.argv) > 1:
        companies = [{"ticker": t.upper(), "name": t.upper()} for t in sys.argv[1:]]
    else:
        companies = DEFAULT_COMPANIES

    result = await run_report(companies, triggered_by="manual")
    print("\n─── Resultado ───")
    print(f"Status          : {result['status']}")
    print(f"Resumos gerados : {result['summaries']}")
    print(f"Fontes com falha: {result['failed_sources'] or 'nenhuma'}")
    print(f"Duração         : {result['duration_seconds']}s")


asyncio.run(main())
