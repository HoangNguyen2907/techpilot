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
