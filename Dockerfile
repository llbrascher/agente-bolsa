FROM python:3.12-slim

WORKDIR /app

# Dependências do sistema (necessárias para asyncpg e greenlet)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python antes de copiar o código (cache de camadas)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Copia o restante do projeto
COPY . .

EXPOSE 8000

# Roda migrações Alembic e sobe o servidor
CMD alembic upgrade head && \
    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
