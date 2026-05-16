from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Report(Base):
    """Histórico de relatórios gerados e enviados."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(
        Enum("success", "partial", "error", name="report_status"),
        nullable=False,
        default="success",
    )
    triggered_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="scheduler"
    )
    tickers_requested: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list
    tickers_ok: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON list
    sources_failed: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    email_subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_sent_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
