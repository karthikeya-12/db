# API image: FastAPI + ingestion worker + connector scheduler.   docker build -t archive-api .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Docling (PDF/Office conversion) needs a few shared libraries.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version* ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY sql ./sql
COPY main.py README.md ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s CMD curl -fs http://localhost:8000/api/health || exit 1
CMD ["uvicorn", "ingestion_pipeline.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
