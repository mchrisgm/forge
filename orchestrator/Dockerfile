# Build context is the repo root (see docker-compose.yml) so /app gets both
# the orchestrator package and the top-level scripts (seed_models.py).
FROM python:3.12-slim

# git is needed by skills_service (shallow clones of skill repos)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY orchestrator/pyproject.toml ./
RUN pip install --no-cache-dir uv && uv pip install --system --no-cache -r pyproject.toml

COPY orchestrator/app ./app
COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
