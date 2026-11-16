# Data
 
Local data artifacts for development live here. The corpus is **Hacker News only**, used to build a copilot that answers questions about trending tech.
 
## Layout
 
```
data/
├── downloads/                      # raw payloads, never edited by hand
│   └── hn/
│       └── <year>/<story_id>.json  # one file per story: full nested comment tree (Algolia /items/{id})
├── bigquery/                       # optional exports of HN metadata and monthly totals
├── tech_dict.yaml                  # technology names, aliases, categories (drives tagging)
└── download_hn.py                  # Algolia downloader (see "Fetching data")
```
 
- `downloads/` holds raw source files, grouped by year of the story.
- Everything else (cleaned rows, tags, trends, chunks, embeddings) lives in **Postgres + pgvector** and can be rebuilt from `downloads/` and BigQuery.
- Downloaded payloads are gitignored because the corpus can get large.
## Sources
 
| Source | Used for | How it is fetched |
|---|---|---|
| BigQuery `bigquery-public-data.hacker_news.full` | Historical backfill of story metadata, monthly totals of all HN stories | SQL, filtered by date, type and score |
| Algolia HN Search API | Full threads for notable stories, daily incremental updates | `data/download_hn.py` |
 
Hacker News is community opinion, not documentation. The copilot must say "trend on Hacker News", not "trend in the industry".
 
## Database (Postgres + pgvector)
 
| Table / view | Content |
|---|---|
| `hn_stories` | cleaned stories: title, url, text, points, num_comments, created_at, kind (`story`/`ask`/`show`) |
| `hn_comments` | flattened comments: story_id, parent_id, depth, text, n_children, quality_score |
| `story_tech` | story to technology mapping (one story can mention several) |
| `hn_totals_monthly` | total HN stories per month, the denominator for normalizing trends |
| `trend_monthly` (materialized view) | technology x month: stories, points, comments |
| `hn_chunks` | text chunks with metadata and `embedding vector(N)`, plus a `tsvector` column for full-text search |
 
Rules of thumb:
 
- Trend numbers always come from SQL (`trend_monthly`), never from vector search.
- `hn_chunks` serves semantic search only ("what do people say about X").
- Chunk types: `title`, `summary` (LLM summary of a thread, generated once and stored), `comment` (only for very notable threads).
- Record the embedding model name and dimension used. Changing the model means re-embedding everything.
## Record fields
 
| Field | Notes |
|---|---|
| `id`, `story_id`, `parent_id`, `depth` | rebuild thread structure |
| `title`, `text`, `url` | content and the link used for citations |
| `created_at` | technical information goes stale quickly, always store it |
| `points`, `num_comments`, `n_children` | quality signals. HN comments have no public score, so comment quality is inferred (length, replies, depth, code or version numbers) |
| `tech_tags` | technologies matched via `tech_dict.yaml` (title first, then body) |
| `fetched_at` | when the thread was downloaded, for reproducibility |
 
## Fetching data
 
Download full threads for stories matching keywords:
 
```
uv run data/download_hn.py -q flutter -q riverpod --since 2021 --min-points 20 --limit 5
```
 
- Output goes to `data/downloads/hn/<year>/<story_id>.json`.
- Re-running is safe: threads already on disk are skipped.
- Use `--limit 5` for a first test, then remove it.
- Keyword search matches title, text and URL, so ambiguous keywords ("go", "swift") bring noise. Filter them during tagging.
- For long historical backfills, prefer BigQuery for metadata and use the downloader for notable threads and daily updates.
## Update schedule
 
| Frequency | Task |
|---|---|
| Daily | fetch new stories (Algolia), load, tag, embed the new chunks |
| Weekly | re-download threads younger than 7 days (comments are still arriving), refresh summaries if the thread changed a lot |
| Monthly | refresh `trend_monthly`, cross-check against BigQuery |
 
## Licensing
 
Hacker News content is user-generated. Review the current terms of Y Combinator / Hacker News and the Algolia API before any commercial use, respect rate limits, and keep a link back to the original thread in every answer.
 