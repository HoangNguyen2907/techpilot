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

## Hacker News ingestion

Run the downloader from the repository root. Its default behavior skips payloads already on disk:

```bash
uv run data/download.py -q bun -q node --since 2025 --min-points 20 --limit 5
```

From `backend/`, run the offline pipeline against `data/downloads/hn/`:

```bash
uv run python -m app.ingest.run_pipeline --all
uv run python -m app.ingest.run_pipeline --load
uv run python -m app.ingest.run_pipeline --tag
uv run python -m app.ingest.run_pipeline --chunk --embed --limit 100
uv run python -m app.ingest.run_pipeline --refresh-trends
```

`--all` processes local payloads; it does not contact Algolia. OpenAI credentials are used by chunk summarization and embedding. Database stages use the direct PostgreSQL connection configured by `DATABASE_URL`.

## SSE reverse proxy

The API sends `X-Accel-Buffering: no` and 15-second comment heartbeats. If NGINX proxies the chat route, disable buffering and allow an idle interval longer than the heartbeat:

```nginx
location = /api/chat/stream {
    proxy_pass http://techpilot_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 60s;
}
```
