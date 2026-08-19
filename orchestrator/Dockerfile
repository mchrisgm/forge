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
# Only the seed catalog is needed inside the image (bootstrap imports
# scripts.seed_models); host-side shell scripts (smoke/preflight) stay out.
# scripts/ has no __init__.py — it is imported as a namespace package, and the
# single-file COPY still creates the directory.
COPY scripts/seed_models.py ./scripts/seed_models.py
# Skills vendored into the repo (e.g. the Scrapling agent skill) — seeded
# into the skills volume on first boot by services/bundled_skills.py.
COPY skills-bundled ./skills-bundled

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
