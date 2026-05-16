import logging
from datetime import datetime, timezone

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

logger = logging.getLogger(__name__)

resend.api_key = settings.resend_api_key

_jinja = Environment(
    loader=FileSystemLoader("app/email/templates"),
    autoescape=select_autoescape(["html"]),
)


def send_report(summaries: list, failed_sources: list[str], triggered_by: str = "scheduler") -> str:
    """Renderiza o template HTML e envia via Resend. Retorna o e-mail ID."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d/%m/%Y %H:%M") + " UTC"

    template = _jinja.get_template("report.html")
    html_body = template.render(
        summaries=summaries,
        failed_sources=failed_sources,
        date_str=date_str,
        triggered_by=triggered_by,
    )

    tickers = ", ".join(s.ticker for s in summaries)
    subject = f"📈 Relatório B3 — {tickers} — {now.strftime('%d/%m %H:%M')}"

    params: resend.Emails.SendParams = {
        "from": settings.email_from,
        "to": settings.email_recipients,
        "subject": subject,
        "html": html_body,
    }

    response = resend.Emails.send(params)
    logger.info("E-mail enviado: id=%s para %s", response["id"], settings.email_recipients)
    return response["id"]
