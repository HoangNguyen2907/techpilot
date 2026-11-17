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

Run these commands from the repository root unless noted otherwise. The downloader
fetches matching stories and their comment trees from Algolia into
`data/downloads/hn/`. Existing payloads are skipped except for stories from the
last 14 days, which are refreshed by default so their points and comment counts
stay current:

```bash
uv run data/download.py --all --since 2025 --min-points 20
```

`--all` searches all stories that meet the point threshold. Use `-q KEYWORD`
more than once to fetch selected topics instead. `--since` sets the oldest
story date; `--min-points` sets the minimum score. `--limit 5` is useful for a
small download trial. To change the refresh window, pass `--refresh-days 30`;
pass `--refresh-days 0` to disable refreshing existing payloads.

From `backend/`, load the downloaded payloads and tag stories:

```bash
uv run python -m app.ingest.run_pipeline --load --tag
```

The first unrestricted `--tag` run tags the corpus. Later runs only process
stories that have not been tagged with the current dictionary version. If story
title, text, or URL changes during loading, its tags are marked stale and
replaced on the next tag run. Points and comment-count updates do not trigger
retagging.

### Pipeline commands

| Command | What it does |
| --- | --- |
| `uv run python -m app.ingest.run_pipeline --load` | Upserts local downloaded stories and comments into Postgres. |
| `uv run python -m app.ingest.run_pipeline --tag` | Tags pending or stale stories, synchronizes monthly totals, and refreshes `trend_monthly`. |
| `uv run python -m app.ingest.run_pipeline --load --tag` | Loads local payloads, then tags stories and refreshes the aggregates. Use this after downloading. |
| `uv run python -m app.ingest.run_pipeline --chunk` | Creates retrieval chunks and summaries for stories that need them. Uses OpenAI. |
| `uv run python -m app.ingest.run_pipeline --embed` | Embeds pending chunks. Uses OpenAI. |
| `uv run python -m app.ingest.run_pipeline --chunk --embed` | Runs chunk generation followed by embedding. |
| `uv run python -m app.ingest.run_pipeline --refresh-trends` | Refreshes only the trend materialized view. |
| `uv run python -m app.ingest.run_pipeline --all` | Runs local load, tagging, chunk generation, and embedding in that order. It does not contact Algolia. |

Add `--limit N` to run a small trial. Limits apply per selected stage;
limited tagging skips monthly-total and trend-view refreshes. Use `--input PATH`
to load payloads from another directory, and `--dictionary PATH` to tag with
another technology dictionary.

The code also provides `run_daily_pipeline(engine, crawl)` in
`app.ingest.run_pipeline`. It holds a nonblocking Postgres advisory lock while
it calls the supplied crawler, tags stories, synchronizes monthly totals, and
refreshes the trend view. A limited run skips the aggregate refresh. This
function is not exposed as a CLI command yet; a scheduler or caller must provide
the crawler callback. The standalone commands above do not acquire this
whole-pipeline lock.

Database commands use the direct PostgreSQL connection configured by
`DATABASE_URL`. Apply schema changes from `backend/` with
`uv run alembic upgrade head` before running the updated tagging pipeline.
OpenAI credentials are used by chunk summarization and embedding.

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
