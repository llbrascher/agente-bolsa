# Agente Bolsa — Contexto para o Claude Code

## O que é este projeto

Agente Python que monitora ações da B3 em horários programáveis:
coleta notícias + sentimento → gera resumo com Claude Sonnet → envia por e-mail via Resend.

## Stack

- **Web**: FastAPI + Jinja2 + HTMX (sem build step de front-end)
- **ORM**: SQLAlchemy async (SQLite em dev, PostgreSQL em prod)
- **Scheduler**: APScheduler com SQLAlchemyJobStore
- **IA**: anthropic SDK (claude-sonnet-4-6)
- **E-mail**: resend SDK
- **HTTP**: httpx (async)

## Estrutura de pastas

```
app/
  config.py       ← pydantic-settings, TODAS as env vars aqui
  database.py     ← engine + session factory + Base + init_db()
  main.py         ← FastAPI app, lifespan, monta static/templates
  models/         ← Company, Schedule, Report (SQLAlchemy)
  sources/        ← uma classe por fonte (brapi, google_news, tavily, reddit, youtube, trends)
  ai/             ← summarizer.py: monta prompt e chama Claude
  email/          ← sender.py (Resend) + templates/report.html (Jinja2)
  tasks/          ← report_task.py: orquestrador coleta→IA→e-mail
  api/            ← rotas FastAPI (companies, schedules, reports, logs)
frontend/
  templates/      ← Jinja2 renderizado pelo FastAPI
  static/         ← CSS e JS servidos por StaticFiles
```

## Convenções

- Código em inglês, comentários e UI em português.
- Toda variável de ambiente lida via `app/config.py` (nunca `os.environ` direto).
- Cada fonte em `app/sources/` herda de `SourceBase` e nunca propaga exceção — retorna lista vazia + loga o erro.
- Relatório com status `partial` quando alguma fonte falhou mas e-mail foi enviado.
- `init_db()` cria tabelas no startup (dev); Alembic para migrações em produção.

## Como rodar localmente

```bash
# 1. Copiar e preencher variáveis
cp .env.example .env

# 2. Criar venv e instalar
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Subir o servidor
uvicorn app.main:app --reload

# Acessar: http://localhost:8000
# Health:  http://localhost:8000/health
# Docs:    http://localhost:8000/docs
```

## Fase atual

**Fase 1 concluída** — fundação (config, banco, modelos, FastAPI básico).
**Fase 2** — fontes de dados (Brapi + Google News) + pipeline mínimo (IA + e-mail).
