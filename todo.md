# TechPilot — Master Implementation Checklist

This checklist is the source of truth for implementing **TechPilot**, an AI copilot that analyzes Hacker News trends and community sentiment around technologies, frameworks, and developer tools.

It aligns directly with your data pipeline in `data/` (`README.md`, `download.py`, `tagger.py`, `tech_dict.yaml`) and the dual-path architecture:
1. **Deterministic Analytics via SQL (`trend_monthly`)**: Trend metrics, story counts, point velocity, and monthly share *always* come from SQL, never from vector search.
2. **Qualitative Sentiment via Hybrid RAG (`hn_chunks`)**: Opinions, switch rationales, and community debates come from hybrid retrieval (`pgvector` + `tsvector` + RRF) over thread summaries and high-quality comments.
3. **Attribution & Grounding**: Citations link directly to Hacker News threads (`news.ycombinator.com/item?id=<id>`), explicitly scoped to "opinion on Hacker News".

---

## Phase 1: Database Schema & Alembic Migrations

Configure Postgres in Supabase with relational trend tables, materialized views, vector embeddings, and full-text search.

- [ ] **1.1 Database Connection & Extension Verification**
  - [x] Verify `DATABASE_URL` session connection in `backend/.env`.
  - [x] Verify Supabase Auth keys (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`).
  - [x] Ensure `OPENAI_API_KEY` is present and configured.
- [x] **1.2 Initialize Alembic Migration Framework**
  - [x] Initialize Alembic in `backend/`: `alembic init alembic`.
  - [x] Update `backend/alembic/env.py` to import SQLAlchemy models and read `app.config.settings.DATABASE_URL`.
- [x] **1.3 Define SQLAlchemy Models (`backend/app/database/models.py`)**
  - [x] `profiles`: User profiles mapped to `auth.users.id`.
  - [x] `chat_threads`: `id` (UUID), `user_id`, `title`, `created_at`, `updated_at`.
  - [x] `chat_messages`: `id` (UUID), `thread_id`, `role`, `content`, `parts` (JSONB for AI SDK), `created_at`.
  - [x] `message_citations`: `id`, `message_id`, `story_id`, `comment_id`, `hn_url`, `quote_text`.
  - [x] `hn_stories`: `id` (HN story ID), `title`, `url`, `text`, `points`, `num_comments`, `author`, `created_at`, `kind` (`story`, `ask`, `show`).
  - [x] `hn_comments`: `id` (HN comment ID), `story_id`, `parent_id`, `depth`, `author`, `text`, `n_children`, `quality_score`, `created_at`.
  - [x] `story_tech`: `id`, `story_id`, `tech` (canonical slug from `tech_dict.yaml`), `category`, `in_title` (boolean), `via` (`direct`, `ambiguous`, `implied`).
  - [x] `hn_totals_monthly`: `month` (date), `total_stories`, `total_points`, `total_comments`.
  - [x] `hn_chunks`: `id` (UUID), `story_id`, `comment_id` (nullable), `chunk_type` (`title`, `summary`, `comment`), `content`, `embedding` (1536-dim vector), `search_vector` (`tsvector`), `quality_score`, `metadata_json`.
- [ ] **1.4 Create Initial Migration with Indexes & Materialized View**
  - [x] Script prepared: `CREATE EXTENSION IF NOT EXISTS vector;`.
  - [x] Script prepared: HNSW vector index on `hn_chunks.embedding` using cosine distance.
  - [x] Script prepared: GIN index on `hn_chunks.search_vector` for full-text search.
  - [x] Script prepared: indexes on `story_tech(tech, story_id)`, `hn_stories(created_at, points)`, `hn_comments(story_id, quality_score)`.
  - [x] Script prepared: `trend_monthly` materialized view with unique index `(tech, month)`.
  - [ ] Apply migration: `uv run alembic upgrade head`.

---

## Phase 2: Ingestion, Tagging & Embedding Pipeline

Connect raw Hacker News downloads to automated tagging, thread summarization, and vector storage.

- [ ] **2.1 Raw Download Ingestion (`backend/ingest/load_raw.py`)**
  - [ ] Read raw Algolia JSON payloads from `data/downloads/hn/<year>/<story_id>.json`.
  - [ ] Parse and sanitize story records (HTML unescape, strip URLs for clean text) into `hn_stories`.
  - [ ] Flatten nested comment trees into `hn_comments` with calculated `depth`, `n_children`, and inferred `quality_score` (length, code blocks, reply count).
  - [ ] Batch upsert stories and comments into Postgres.
- [ ] **2.2 Automated Entity Tagging Integration (`backend/ingest/tag_stories.py`)**
  - [ ] Integrate `data/tagger.py` and `data/tech_dict.yaml` into the backend ingestion workflow.
  - [ ] Run `tag_story()` on all ingested stories:
    * Match canonical aliases, handle ambiguous terms with context window (e.g., "Go", "Swift", "Bloc"), and expand `implies`.
  - [ ] Upsert results into `story_tech`.
  - [ ] Refresh the `trend_monthly` materialized view.
- [ ] **2.3 Chunk Generation & LLM Summarization (`backend/ingest/chunk_and_embed.py`)**
  - [ ] Generate `title` chunks for notable stories (points >= threshold).
  - [ ] Generate `summary` chunks: For high-engagement threads (e.g., num_comments >= 20 or points >= 50), generate a concise LLM summary synthesizing consensus, pros/cons, and key arguments.
  - [ ] Generate `comment` chunks: Extract top-tier comments (high `quality_score`) with their parent context.
  - [ ] Embed chunks using OpenAI `text-embedding-3-small` (1536-dim) in batches.
  - [ ] Populate `hn_chunks` with text, embeddings, generated `search_vector`, and metadata (author, story_id, points, created_at).

---

## Phase 3: Dual-Mode Retrieval Engine

Build the two core data retrieval mechanisms: deterministic SQL metrics and qualitative hybrid search.

- [ ] **3.1 SQL Metrics Engine (`backend/app/retrieval/trends.py`)**
  - [ ] Query monthly and yearly metrics for any technology from `trend_monthly`.
  - [ ] Compare two or more technologies over time (e.g., "Flutter vs React Native", "Bun vs Node").
  - [ ] Calculate velocity (growth rate of mentions/points month-over-month).
  - [ ] Extract top breakout technologies in a given category or timeframe.
- [ ] **3.2 Hybrid Community Sentiment Retriever (`backend/app/retrieval/hybrid.py`)**
  - [ ] `pgvector` semantic similarity search over `hn_chunks` with cosine distance.
  - [ ] Postgres full-text keyword search over `hn_chunks.search_vector` with websearch ranking.
  - [ ] Reciprocal Rank Fusion (`fusion.py`) to combine vector and keyword rankings ($RRF\_score = \sum \frac{1}{60 + rank_i}$).
  - [ ] Metadata filtering by `tech_tag`, `chunk_type` (`summary` vs `comment`), and `date_range`.
  - [ ] Return structured passage results with HN permalinks (`https://news.ycombinator.com/item?id=...`).
- [ ] **3.3 Unit Tests for Retrieval Layer (`backend/tests/retrieval/`)**
  - [ ] Unit tests for RRF fusion logic and tie-breaking.
  - [ ] Unit tests for trend SQL query generation and parameter sanitation.

---

## Phase 4: PydanticAI Assistant & FastAPI Streaming Service

Build the typed agent, enforce grounding and attribution invariants, and implement streaming SSE.

- [ ] **4.1 Backend Config & Auth Dependencies**
  - [ ] `backend/app/config.py`: Pydantic settings loading all environment variables on boot.
  - [ ] `backend/app/auth/dependencies.py`: Supabase JWT validator extracting authenticated `user_id`.
  - [ ] `backend/app/database/supabase.py`: Scoped Supabase client helpers.
- [ ] **4.2 Grounding & Attribution Contract (`backend/app/grounding/`)**
  - [ ] `validator.py`: Validate that:
    1. Trend numbers and statistics cite SQL results from `trend_monthly`.
    2. Qualitative claims cite retrieved chunk IDs with valid HN comment/story permalinks.
    3. The assistant frames answers as "sentiment on Hacker News", never universal industry gospel.
  - [ ] Refusal handling: If the corpus lacks data for a requested technology or timeframe, explicitly state that HN discussions are unavailable rather than speculating.
- [ ] **4.3 PydanticAI Agent Definition (`backend/app/assistant/`)**
  - [ ] `deps.py`: `TechPilotAgentDeps` with `user_id`, `trend_engine`, `hybrid_retriever`, `grounding_validator`.
  - [ ] Tools registered on agent:
    * `get_technology_trends(technologies, start_date, end_date)`: Deterministic SQL tool.
    * `search_community_discussions(query, tech_tags, chunk_types)`: Hybrid RAG tool.
    * `get_thread_context(story_id)`: Fetches story metadata and top comments.
  - [ ] `instructions.md`: System prompt enforcing:
    * Always distinguish SQL facts (mentions, points) from developer opinions.
    * Format inline citations as `[^1]` mapping to HN item URLs.
    * Keep answers analytical, neutral, and clear.
- [ ] **4.4 Turn Orchestrator & Streaming Endpoint (`backend/app/chat/`)**
  - [ ] `streaming.py`: Stream text tokens and structured metadata (tool calls, citations, trend data points) using AI SDK UI format.
  - [ ] `orchestrator.py`: Coordinate turn execution, run agent, validate output, persist conversation to `chat_messages` and `message_citations`.
  - [ ] `app/api/chat.py`:
    * `POST /chat/stream`: Stream assistant response.
    * `GET /chat/threads`: List user chat threads.
    * `GET /chat/threads/{id}/messages`: Fetch full message history with citations.

---

## Phase 5: Frontend React SPA (Vite + shadcn + AI SDK)

Create the user interface for exploring trends, chatting with the copilot, and inspecting cited HN comments.

- [ ] **5.1 Frontend Scaffolding (`frontend/`)**
  - [ ] Initialize Vite React + TypeScript template: `pnpm create vite . --template react-ts`.
  - [ ] Configure `pnpm` with minimum release age in `.npmrc`.
  - [ ] Install dependencies: `react-router-dom`, `@supabase/supabase-js`, `ai`, `@ai-sdk/react`, `lucide-react`.
  - [ ] Initialize Tailwind CSS and shadcn/ui components (`button`, `input`, `badge`, `sheet`, `card`, `scroll-area`, `tabs`).
- [ ] **5.2 Core Infrastructure (`frontend/src/lib/`)**
  - [ ] `env.ts`: Validate `VITE_API_BASE_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.
  - [ ] `supabase.ts`: Browser Supabase client for email login/logout.
  - [ ] `http.ts` & `api.ts`: Typed client injecting Supabase JWT into all requests to FastAPI.
- [ ] **5.3 Authentication & Thread Layout**
  - [ ] Clean Supabase email login screen (no passwords in client code, magic link or secure auth).
  - [ ] Protected route wrapper.
  - [ ] Collapsible sidebar with historical threads and "New Thread" button.
- [ ] **5.4 Interactive Chat Interface**
  - [ ] Streaming chat view using `@ai-sdk/react` (`useChat`).
  - [ ] Markdown rendering with syntax highlighting and clickable citation badges `[^1]`.
  - [ ] Inline Trend Metric Cards: Render small metric cards for queries returning `trend_monthly` SQL data (e.g. story count, point volume).
- [ ] **5.5 Hacker News Source Inspector Drawer**
  - [ ] Clicking a citation badge opens a side-drawer showing:
    * Story title, author, points, date, and link to `news.ycombinator.com/item?id=...`.
    * Exact comment text or thread summary cited.
    * Matched technology tags.

---

## Phase 6: End-to-End Verification & Benchmark Queries

Test the copilot against real developer questions to confirm accuracy and grounding.

- [ ] **6.1 Trend & Metric Accuracy Tests**
  - [ ] Test query: *"How has mention volume for Bun vs Node.js evolved on Hacker News over the last 3 years?"*
    * Verify answer uses exact numbers from `trend_monthly` SQL and does not hallucinate counts.
- [ ] **6.2 Sentiment & Discussion Synthesis Tests**
  - [ ] Test query: *"Why did developers say they migrated from Redux to Zustand or Jotai?"*
    * Verify answer cites specific comments with valid HN comment IDs.
- [ ] **6.3 Negative & Refusal Tests**
  - [ ] Test query on an obscure or fictitious tool (e.g., *"What do HN users think about SuperFakeFrameworkX?"*).
    * Verify the assistant clearly reports no mentions exist on Hacker News instead of fabricating sentiment.
- [ ] **6.4 Code Quality & Build Checks**
  - [ ] Backend: `uv run pytest` passes (unit tests for RRF, trend queries, grounding validator).
  - [ ] Backend: `uv run ruff check .` and `uv run ruff format --check .`.
  - [ ] Frontend: `pnpm tsc --noEmit` and `pnpm lint`.
