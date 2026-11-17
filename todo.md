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
  - [x] Apply migration: `uv run alembic upgrade head`.

---

## Phase 2: Auth (Fullstack)

Implement Supabase Auth across the frontend and backend, and provide the verified user identity used by later chat routes.

- [ ] **2.1 Supabase Auth Configuration**
  - [x] Configure allowed application redirect URLs and email sign-in settings in Supabase.
  - [x] Confirm the backend has Supabase URL and anon/service-role keys in its settings, and the frontend has its public Supabase URL and anon key.
- [ ] **2.2 Frontend Sign-In & Session Management**
  - [x] Create email sign-in and sign-out flows using Supabase Auth (magic link or passwordless OTP).
  - [x] Persist and restore the session; handle expired sessions and auth callbacks.
  - [x] Add protected routes and redirect unauthenticated users to sign-in.
- [x] **2.3 Backend JWT Authentication**
  - [x] Validate Supabase access tokens on protected API routes and expose the authenticated `user_id` dependency.
  - [x] Reject missing, invalid, or expired tokens with `401 Unauthorized`.
  - [x] Expose the verified identity for downstream authorization; chat thread ownership rules are defined in Phase 3.
- [ ] **2.4 Auth & RLS Integration**
  - [x] Ensure a profile row is created or synchronized for each authenticated Supabase user.
  - [x] Verify RLS policies restrict threads, messages, and citations to their owner while allowing intended read-only HN corpus access.
  - [x] Add tests for valid, invalid, missing, and expired tokens.

---

## Phase 3: Chat Shell (Fullstack)

Build the first authenticated chat experience end to end, with owned threads, AI SDK message compatibility, streaming replies, and persistence.

- [x] **3.1 Backend Chat Thread CRUD (`backend/app/api/chat.py`)**
  - [x] Create, list, retrieve, rename, and delete the authenticated user's chat threads.
  - [x] Derive `user_id` from `CurrentUserDep`; never accept ownership from request data.
  - [x] Return `403 Forbidden` when a user tries to access or mutate another user's thread.
- [x] **3.2 Backend Chat Streaming (`backend/app/api/chat.py`)**
  - [x] Implement `POST /api/chat/stream` using the `threadId` and single `content` request contract in `todo-phase3-BE.md`.
  - [x] Validate thread ownership before processing the request.
  - [x] Stream structured SSE title, delta, done, and error events.
- [x] **3.3 Chat Persistence (`backend/app/chat/`)**
  - [x] Persist the submitted user message and completed assistant reply in `chat_messages`.
  - [x] Load server-side thread history for model context, associate messages with the owned thread, and update its activity timestamp.
- [ ] **3.4 Frontend Chat Shell (`frontend/src/`)**
  - [x] Add an authenticated chat view with thread list, new thread, rename, delete, and thread selection.
  - [x] Submit AI SDK UI messages to the backend with the current Supabase access token.
  - [x] Render the streamed assistant reply and restore a thread's message history.

---

## Phase 4: Ingestion, Tagging & Embedding Pipeline

Connect raw Hacker News downloads to automated tagging, thread summarization, and vector storage.

- [ ] **4.1 Raw Download Ingestion (`backend/ingest/load_raw.py`)**
  - [ ] Read raw Algolia JSON payloads from `data/downloads/hn/<year>/<story_id>.json`.
  - [ ] Parse and sanitize story records (HTML unescape, strip URLs for clean text) into `hn_stories`.
  - [ ] Flatten nested comment trees into `hn_comments` with calculated `depth`, `n_children`, and inferred `quality_score` (length, code blocks, reply count).
  - [ ] Batch upsert stories and comments into Postgres.
- [ ] **4.2 Automated Entity Tagging Integration (`backend/ingest/tag_stories.py`)**
  - [ ] Integrate `data/tagger.py` and `data/tech_dict.yaml` into the backend ingestion workflow.
  - [ ] Run `tag_story()` on all ingested stories:
    * Match canonical aliases, handle ambiguous terms with context window (e.g., "Go", "Swift", "Bloc"), and expand `implies`.
  - [ ] Upsert results into `story_tech`.
  - [ ] Refresh the `trend_monthly` materialized view.
- [ ] **4.3 Chunk Generation & LLM Summarization (`backend/ingest/chunk_and_embed.py`)**
  - [ ] Generate `title` chunks for notable stories (points >= threshold).
  - [ ] Generate `summary` chunks: For high-engagement threads (e.g., num_comments >= 20 or points >= 50), generate a concise LLM summary synthesizing consensus, pros/cons, and key arguments.
  - [ ] Generate `comment` chunks: Extract top-tier comments (high `quality_score`) with their parent context.
  - [ ] Embed chunks using OpenAI `text-embedding-3-small` (1536-dim) in batches.
  - [ ] Populate `hn_chunks` with text, embeddings, generated `search_vector`, and metadata (author, story_id, points, created_at).

---

## Phase 5: Dual-Mode Retrieval Engine

Build the two core data retrieval mechanisms: deterministic SQL metrics and qualitative hybrid search.

- [ ] **5.1 SQL Metrics Engine (`backend/app/retrieval/trends.py`)**
  - [ ] Query monthly and yearly metrics for any technology from `trend_monthly`.
  - [ ] Compare two or more technologies over time (e.g., "Flutter vs React Native", "Bun vs Node").
  - [ ] Calculate velocity (growth rate of mentions/points month-over-month).
  - [ ] Extract top breakout technologies in a given category or timeframe.
- [ ] **5.2 Hybrid Community Sentiment Retriever (`backend/app/retrieval/hybrid.py`)**
  - [ ] `pgvector` semantic similarity search over `hn_chunks` with cosine distance.
  - [ ] Postgres full-text keyword search over `hn_chunks.search_vector` with websearch ranking.
  - [ ] Reciprocal Rank Fusion (`fusion.py`) to combine vector and keyword rankings ($RRF\_score = \sum \frac{1}{60 + rank_i}$).
  - [ ] Metadata filtering by `tech_tag`, `chunk_type` (`summary` vs `comment`), and `date_range`.
  - [ ] Return structured passage results with HN permalinks (`https://news.ycombinator.com/item?id=...`).
- [ ] **5.3 Unit Tests for Retrieval Layer (`backend/tests/retrieval/`)**
  - [ ] Unit tests for RRF fusion logic and tie-breaking.
  - [ ] Unit tests for trend SQL query generation and parameter sanitation.

---

## Phase 6: PydanticAI Assistant & Chat Stream Integration

Build the typed agent and connect its grounded answers to the Chat Shell from Phase 3.

- [ ] **6.1 Grounding & Attribution Contract (`backend/app/grounding/`)**
  - [ ] `validator.py`: Validate that:
    1. Trend numbers and statistics cite SQL results from `trend_monthly`.
    2. Qualitative claims cite retrieved chunk IDs with valid HN comment/story permalinks.
    3. The assistant frames answers as "sentiment on Hacker News", never universal industry gospel.
  - [ ] Refusal handling: If the corpus lacks data for a requested technology or timeframe, explicitly state that HN discussions are unavailable rather than speculating.
- [ ] **6.2 PydanticAI Agent Definition (`backend/app/assistant/`)**
  - [ ] `deps.py`: `TechPilotAgentDeps` with `user_id`, `trend_engine`, `hybrid_retriever`, `grounding_validator`.
  - [ ] Tools registered on agent:
    * `get_technology_trends(technologies, start_date, end_date)`: Deterministic SQL tool.
    * `search_community_discussions(query, tech_tags, chunk_types)`: Hybrid RAG tool.
    * `get_thread_context(story_id)`: Fetches story metadata and top comments.
  - [ ] `instructions.md`: System prompt enforcing:
    * Always distinguish SQL facts (mentions, points) from developer opinions.
    * Format inline citations as `[^1]` mapping to HN item URLs.
    * Keep answers analytical, neutral, and clear.
- [ ] **6.3 Assistant Integration with Chat Stream (`backend/app/chat/`)**
  - [ ] Connect the PydanticAI agent and grounding checks to the existing `POST /chat/stream` endpoint from Phase 3.
  - [ ] `streaming.py`: Stream agent output and structured tool, citation, and trend metadata in the AI SDK UI message format.
  - [ ] `orchestrator.py`: Run the agent, validate grounded output, and persist `message_citations` after successful completion.
  - [ ] Reuse Phase 3 thread ownership, chat message persistence, and thread CRUD; do not add a second chat route or persistence path.

---

## Phase 7: Frontend Research UI (React + shadcn + AI SDK)

Polish the Phase 3 chat shell with research-specific response rendering and Hacker News source inspection.

- [ ] **7.1 Frontend Design System (`frontend/src/`)**
  - [ ] Configure Tailwind CSS and shadcn/ui primitives used by the product (button, input, badge, sheet, card, scroll-area, tabs).
  - [ ] Apply responsive styling and shared layout components to the existing authenticated chat shell.
- [ ] **7.2 Research Response Rendering (`frontend/src/`)**
  - [ ] Render assistant Markdown with syntax highlighting and clickable citation badges `[^1]`.
  - [ ] Render inline trend metric cards for structured `trend_monthly` data.
- [ ] **7.3 Hacker News Source Inspector Drawer (`frontend/src/`)**
  - [ ] Open the cited HN source when a citation badge is selected, including story metadata, exact cited text, and matched technology tags.
  - [ ] Link to the Hacker News item permalink.

---

## Phase 8: End-to-End Verification & Benchmark Queries

Test the copilot against real developer questions to confirm accuracy and grounding.

- [ ] **8.1 Trend & Metric Accuracy Tests**
  - [ ] Test query: *"How has mention volume for Bun vs Node.js evolved on Hacker News over the last 3 years?"*
    * Verify answer uses exact numbers from `trend_monthly` SQL and does not hallucinate counts.
- [ ] **8.2 Sentiment & Discussion Synthesis Tests**
  - [ ] Test query: *"Why did developers say they migrated from Redux to Zustand or Jotai?"*
    * Verify answer cites specific comments with valid HN comment IDs.
- [ ] **8.3 Negative & Refusal Tests**
  - [ ] Test query on an obscure or fictitious tool (e.g., *"What do HN users think about SuperFakeFrameworkX?"*).
    * Verify the assistant clearly reports no mentions exist on Hacker News instead of fabricating sentiment.
- [ ] **8.4 Code Quality & Build Checks**
  - [ ] Backend: `uv run pytest` passes (unit tests for RRF, trend queries, grounding validator).
  - [ ] Backend: `uv run ruff check .` and `uv run ruff format --check .`.
  - [ ] Frontend: `pnpm tsc --noEmit` and `pnpm lint`.
