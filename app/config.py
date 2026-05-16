from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Agente Bolsa"
    debug: bool = False
    secret_key: str = "troque-esta-chave-em-producao"

    # Banco de dados
    database_url: str = "sqlite+aiosqlite:///./agente_bolsa.db"

    # Claude API
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-6"

    # Resend (e-mail)
    resend_api_key: str
    email_from: str = "bolsa@seudominio.com.br"
    email_to: str  # destinatário padrão; múltiplos separados por vírgula

    # Brapi.dev
    brapi_token: str = ""  # gratuito sem token; token aumenta o rate limit

    # Tavily (opcional — se vazio, fonte é pulada)
    tavily_api_key: str = ""

    # Reddit (opcional — se vazio, fonte é pulada)
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "AgenteBolsa/0.1"

    # YouTube / Google (opcional — se vazio, fonte é pulada)
    youtube_api_key: str = ""

    # Substack (opcional — lista de URLs de feeds RSS separados por vírgula)
    substack_feeds: str = ""

    @property
    def email_recipients(self) -> list[str]:
        return [e.strip() for e in self.email_to.split(",") if e.strip()]

    @property
    def substack_feed_list(self) -> list[str]:
        return [u.strip() for u in self.substack_feeds.split(",") if u.strip()]


settings = Settings()  # type: ignore[call-arg]
