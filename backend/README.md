# TechPilot Backend

FastAPI service for TechPilot, with Supabase Postgres migrations and AI configuration.

## Setup

From this directory:

```bash
uv sync
cp .env.example .env
```

Set the Supabase credentials and direct Postgres connection string in `.env`. Use the direct database connection for Alembic, not the transaction pooler URL. `ALLOWED_ORIGINS` accepts comma-separated frontend origins.

## Run

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

The API is available at `http://localhost:8000`. Health check: `GET /health`. Interactive API docs: `/docs`.
