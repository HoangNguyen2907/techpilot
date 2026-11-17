import asyncio
import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, UUID, uuid5

from openai import APIStatusError, AsyncOpenAI, RateLimitError
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    and_,
    case,
    delete,
    func,
    or_,
    select,
    text,
    tuple_,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import column

from app.config import settings
from app.database.models import HNChunk, HNComment, HNStory, StoryTech
from app.database.session import create_session_factory
from app.ingest.load_raw import clean_hn_text
from app.ingest.openai_utils import _is_transient, create_with_retries
from app.ingest.summarizer import summarize_thread

logger = logging.getLogger(__name__)
CHUNK_BATCH_SIZE = 50
MAX_EMBEDDING_INPUT_BYTES = 6_000
MAX_EMBEDDING_BATCH_BYTES = 200_000
MAX_COMMENTS_PER_STORY = 100
SUMMARY_COMMENT_COUNT = 40
SUMMARY_CONCURRENCY = 5
STORY_BATCH_SIZE = 100
MIN_COMMENT_QUALITY = 0.65
MIN_COMMENT_CHARS = 20
BODY_EXCERPT_CHARS = 1_000
TOP_COMMENTS_FOR_NOTABLE = 3
DEFAULT_QUALITY = 0.5
MAX_EMBEDDING_ATTEMPTS = 3
UNTAGGED_DELETE_THRESHOLD = 0.2
SHORT_REPLY_CHARS = 140
PARENT_EXCERPT_CHARS = 300
EMBEDDING_INPUT_VERSION = "utf8-prefix-6000-v1"
CHUNK_LOCK_KEY = "techpilot.hn.chunk_stories.v1"
EMBED_LOCK_KEY = "techpilot.hn.embed_pending.v1"


class CommentSnapshot(TypedDict):
    id: int
    parent_id: int | None
    parent_text: str
    author: str
    text: str
    quality_score: float


class StorySnapshot(TypedDict):
    id: int
    title: str
    url: str | None
    text: str
    author: str | None
    points: int
    num_comments: int
    created_at: datetime | None
    technologies: list[dict[str, str]]
    comments: list[CommentSnapshot]


class ChunkRow(TypedDict):
    id: UUID
    story_id: int
    comment_id: int | None
    chunk_type: str
    content: str
    content_hash: str
    embedding: None
    embedding_version: None
    embedding_error: None
    embedding_attempts: int
    quality_score: float
    metadata_json: dict[str, Any]


class PendingChunk(TypedDict):
    id: UUID
    content: str
    content_hash: str
    embedding_version: str | None
    created_at: datetime


@dataclass
class StoryChunks:
    rows: list[ChunkRow]
    keep_ids: set[UUID]
    summary_generated: int = 0
    summary_reused: int = 0
    summary_failed: Exception | None = None


def chunk_id(
    chunk_type: str, story_id: int, comment_id: int | None = None, part_index: int = 0
) -> UUID:
    base = f"techpilot:hn-chunk:{chunk_type}:{story_id}:{comment_id or ''}"
    key = base if part_index <= 0 else f"{base}:part:{part_index}"
    return uuid5(NAMESPACE_URL, key)


def truncate_utf8(text: str, max_bytes: int = MAX_EMBEDDING_INPUT_BYTES) -> str:
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def embedding_version() -> str:
    config = (
        f"{settings.OPENAI_EMBEDDING_MODEL}|"
        f"{settings.OPENAI_EMBEDDING_DIMENSIONS}|{EMBEDDING_INPUT_VERSION}"
    )
    return hashlib.sha256(config.encode("utf-8")).hexdigest()


def is_notable(story: StorySnapshot) -> bool:
    return story["num_comments"] >= 50 or story["points"] >= 50


def _quality(value: float | None) -> float:
    return value if value is not None else 0.0


def _chunk_metadata(
    story: StorySnapshot,
    item_id: int,
    author: str | None,
    *,
    parent_id: int | None = None,
    part_index: int = 0,
) -> dict[str, Any]:
    return {
        "hn_url": f"https://news.ycombinator.com/item?id={item_id}",
        "story_url": f"https://news.ycombinator.com/item?id={story['id']}",
        "author": author or story["author"],
        "points": story["points"],
        "num_comments": story["num_comments"],
        "created_at": story["created_at"].isoformat() if story["created_at"] else None,
        "technologies": [tag["tech"] for tag in story["technologies"]],
        "parent_id": parent_id,
        "part_index": part_index,
    }


def chunk_row(
    story: StorySnapshot,
    chunk_type: str,
    content: str,
    quality_score: float,
    comment: CommentSnapshot | None = None,
    *,
    part_index: int = 0,
) -> ChunkRow:
    comment_id = comment["id"] if comment else None
    item_id = comment_id if comment_id is not None else story["id"]
    author = comment["author"] if comment else None
    return {
        "id": chunk_id(chunk_type, story["id"], comment_id, part_index),
        "story_id": story["id"],
        "comment_id": comment_id,
        "chunk_type": chunk_type,
        "content": content,
        "content_hash": content_hash(content),
        "embedding": None,
        "embedding_version": None,
        "embedding_error": None,
        "embedding_attempts": 0,
        "quality_score": quality_score,
        "metadata_json": _chunk_metadata(
            story,
            item_id,
            author,
            parent_id=comment["parent_id"] if comment else None,
            part_index=part_index,
        ),
    }


def _story_snapshots(
    session: Session, stories: list[tuple[Any, ...]]
) -> list[StorySnapshot]:
    if not stories:
        return []
    story_ids = [row.id for row in stories]
    technologies: dict[int, list[dict[str, str]]] = {
        story_id: [] for story_id in story_ids
    }
    for row in session.execute(
        select(StoryTech.story_id, StoryTech.tech, StoryTech.category)
        .where(StoryTech.story_id.in_(story_ids))
        .order_by(StoryTech.story_id, StoryTech.tech)
    ):
        technologies[row.story_id].append({"tech": row.tech, "category": row.category})

    parent_comment = HNComment.__table__.alias("parent_comment")
    ranked_comments = (
        select(
            HNComment.id,
            HNComment.story_id,
            HNComment.parent_id,
            HNComment.author,
            HNComment.text,
            HNComment.quality_score,
            parent_comment.c.text.label("parent_text"),
            func.row_number()
            .over(
                partition_by=HNComment.story_id,
                order_by=(HNComment.quality_score.desc().nulls_last(), HNComment.id),
            )
            .label("rank"),
        )
        .outerjoin(parent_comment, parent_comment.c.id == HNComment.parent_id)
        .where(HNComment.story_id.in_(story_ids))
        .subquery()
    )
    comments: dict[int, list[CommentSnapshot]] = {
        story_id: [] for story_id in story_ids
    }
    for row in session.execute(
        select(ranked_comments)
        .where(ranked_comments.c.rank <= MAX_COMMENTS_PER_STORY)
        .order_by(ranked_comments.c.story_id, ranked_comments.c.rank)
    ):
        cleaned = clean_hn_text(row.text)
        if len(cleaned) < MIN_COMMENT_CHARS:
            continue
        comments[row.story_id].append(
            {
                "id": row.id,
                "parent_id": row.parent_id,
                "parent_text": clean_hn_text(row.parent_text),
                "author": row.author or "unknown",
                "text": cleaned,
                "quality_score": _quality(row.quality_score),
            }
        )

    return [
        {
            "id": row.id,
            "title": clean_hn_text(row.title) or "",
            "url": row.url,
            "text": clean_hn_text(row.text),
            "author": row.author,
            "points": row.points or 0,
            "num_comments": row.num_comments or 0,
            "created_at": row.created_at,
            "technologies": technologies[row.id],
            "comments": comments[row.id],
        }
        for row in stories
    ]


def _split_utf8(text: str, max_bytes: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in text:
        size = len(character.encode("utf-8"))
        if current and current_bytes + size > max_bytes:
            parts.append("".join(current))
            current, current_bytes = [], 0
        current.append(character)
        current_bytes += size
    if current:
        parts.append("".join(current))
    return parts or [""]


def _story_chunks(story: StorySnapshot) -> list[ChunkRow]:
    technologies = story["technologies"]
    tag_names = [tag["tech"] for tag in technologies]
    categories = sorted({tag["category"] for tag in technologies})
    title_content = (
        f"Story Title: {story['title']}\n"
        f"URL: {story['url'] or 'N/A'}\n"
        f"Category: {', '.join(categories)} | Tags: {', '.join(tag_names)}\n"
        f"{story['text'][:BODY_EXCERPT_CHARS]}"
    )
    chunks = [chunk_row(story, "title", title_content, DEFAULT_QUALITY)]

    notable = is_notable(story)
    comments = story["comments"]
    selected_comments = [
        comment
        for comment in comments
        if _quality(comment["quality_score"]) >= MIN_COMMENT_QUALITY
    ]
    if notable:
        selected_ids = {comment["id"] for comment in selected_comments}
        selected_comments.extend(
            comment
            for comment in comments[:TOP_COMMENTS_FOR_NOTABLE]
            if comment["id"] not in selected_ids
        )
    for comment in selected_comments:
        parent_context = ""
        if len(comment["text"]) < SHORT_REPLY_CHARS and comment["parent_text"]:
            parent_excerpt = comment["parent_text"][:PARENT_EXCERPT_CHARS]
            parent_context = f"Parent comment context: {parent_excerpt}\n"
        base = (
            f"Comment on '{story['title']}' by {comment['author']} "
            f"(HN ID: {comment['id']}):\n{parent_context}"
        )
        base = truncate_utf8(base, MAX_EMBEDDING_INPUT_BYTES // 2)
        content_parts = _split_utf8(
            comment["text"],
            max(1, MAX_EMBEDDING_INPUT_BYTES - len(base.encode("utf-8"))),
        )
        for part_index, part in enumerate(content_parts):
            content = f"{base}{part}"
            chunks.append(
                chunk_row(
                    story,
                    "comment",
                    content,
                    _quality(comment["quality_score"]),
                    comment,
                    part_index=part_index,
                )
            )
    return chunks


def _summary_source_hash(story: StorySnapshot) -> str:
    summary_inputs = {
        "title": story["title"],
        "text": story["text"],
        "comments": [
            {"author": comment["author"], "text": comment["text"]}
            for comment in story["comments"][:SUMMARY_COMMENT_COUNT]
        ],
        "technologies": [tag["tech"] for tag in story["technologies"]],
    }
    serialized = json.dumps(
        summary_inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _summary_chunk(story: StorySnapshot, summary: str, source_hash: str) -> ChunkRow:
    content = (
        f"Thread Summary: {story['title']} (HN ID: {story['id']})\n"
        f"Discussed Technologies: "
        f"{', '.join(tag['tech'] for tag in story['technologies'])}\n"
        f"Key Community Perspectives:\n{summary}"
    )
    row = chunk_row(story, "summary", content, DEFAULT_QUALITY)
    row["metadata_json"]["source_hash"] = source_hash
    return row


def _upsert_chunks(session: Session, rows: list[ChunkRow]) -> int:
    if not rows:
        return 0
    statement = insert(HNChunk).values(rows)
    preserve_embedding = and_(
        HNChunk.content_hash == statement.excluded.content_hash,
        HNChunk.embedding_version == embedding_version(),
    )
    changed = or_(
        HNChunk.content.is_distinct_from(statement.excluded.content),
        HNChunk.quality_score.is_distinct_from(statement.excluded.quality_score),
        HNChunk.metadata_json.is_distinct_from(statement.excluded.metadata_json),
    )
    statement = statement.on_conflict_do_update(
        index_elements=[HNChunk.id],
        set_={
            "story_id": statement.excluded.story_id,
            "comment_id": statement.excluded.comment_id,
            "chunk_type": statement.excluded.chunk_type,
            "content": statement.excluded.content,
            "content_hash": statement.excluded.content_hash,
            "embedding": case((preserve_embedding, HNChunk.embedding), else_=None),
            "embedding_version": case(
                (preserve_embedding, HNChunk.embedding_version), else_=None
            ),
            "embedding_error": case(
                (preserve_embedding, HNChunk.embedding_error), else_=None
            ),
            "embedding_attempts": case(
                (preserve_embedding, HNChunk.embedding_attempts), else_=0
            ),
            "quality_score": statement.excluded.quality_score,
            "metadata_json": statement.excluded.metadata_json,
        },
        where=changed,
    )
    result = session.execute(statement)
    return result.rowcount or 0


def _existing_summaries(
    session: Session, story_ids: list[int]
) -> dict[int, tuple[UUID, str | None, dict[str, Any] | None]]:
    rows = session.execute(
        select(
            HNChunk.story_id, HNChunk.id, HNChunk.content, HNChunk.metadata_json
        ).where(HNChunk.story_id.in_(story_ids), HNChunk.chunk_type == "summary")
    )
    return {row.story_id: (row.id, row.content, row.metadata_json) for row in rows}


def _summary_matches(
    story: StorySnapshot,
    existing: tuple[UUID, str | None, dict[str, Any] | None] | None,
) -> bool:
    return bool(
        existing
        and existing[2]
        and existing[2].get("source_hash") == _summary_source_hash(story)
    )


async def _chunks_for_story(
    story: StorySnapshot,
    client: AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    existing_summary: tuple[UUID, str | None, dict[str, Any] | None] | None,
) -> StoryChunks:
    rows = _story_chunks(story)
    keep_ids: set[UUID] = set()
    if not is_notable(story):
        return StoryChunks(rows, keep_ids)

    source_hash = _summary_source_hash(story)
    if _summary_matches(story, existing_summary):
        keep_ids.add(existing_summary[0])
        return StoryChunks(rows, keep_ids, summary_reused=1)

    try:
        async with semaphore:
            summary = await summarize_thread(
                client,
                story["id"],
                story["title"],
                [tag["tech"] for tag in story["technologies"]],
                story["text"],
                [
                    {"author": comment["author"], "text": comment["text"]}
                    for comment in story["comments"][:SUMMARY_COMMENT_COUNT]
                ],
            )
    except Exception as error:
        logger.exception("Summary generation failed for story_id=%s", story["id"])
        if existing_summary:
            keep_ids.add(existing_summary[0])
        return StoryChunks(rows, keep_ids, summary_failed=error)

    rows.append(_summary_chunk(story, summary, source_hash))
    return StoryChunks(rows, keep_ids, summary_generated=1)


def _try_advisory_lock(engine: Engine, key: str) -> Any:
    @contextmanager
    def lock_scope() -> Iterator[None]:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": key},
            ).scalar_one()
            if not acquired:
                raise RuntimeError(f"Pipeline lock is already held: {key}")
            try:
                yield
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": key},
                )

    return lock_scope()


def _remove_untagged_chunks(
    session: Session,
    force: bool = False,
    threshold: float = UNTAGGED_DELETE_THRESHOLD,
) -> int:
    total = session.scalar(select(func.count()).select_from(HNChunk)) or 0
    untagged = select(StoryTech.story_id).distinct()
    stale = (
        session.scalar(
            select(func.count())
            .select_from(HNChunk)
            .where(~HNChunk.story_id.in_(untagged))
        )
        or 0
    )
    if total and stale / total > threshold and not force:
        raise RuntimeError(
            f"Refusing to delete {stale}/{total} untagged chunks "
            f"({stale / total:.1%} exceeds {threshold:.1%}); pass force=True to proceed"
        )
    if stale:
        result = session.execute(delete(HNChunk).where(~HNChunk.story_id.in_(untagged)))
        deleted = result.rowcount or 0
        logger.info("Removed %d chunks from stories without technology tags", deleted)
        return deleted
    return 0


def _delete_stale_chunks_batch(session: Session, desired: dict[int, set[UUID]]) -> int:
    if not desired:
        return 0
    keep_conditions = [
        and_(HNChunk.story_id == story_id, HNChunk.id.in_(chunk_ids))
        for story_id, chunk_ids in desired.items()
        if chunk_ids
    ]
    story_ids = list(desired)
    statement = delete(HNChunk).where(HNChunk.story_id.in_(story_ids))
    if keep_conditions:
        statement = statement.where(~or_(*keep_conditions))
    result = session.execute(statement)
    return result.rowcount or 0


def _persist_story_outcomes(
    sessions: sessionmaker[Session],
    stories: list[StorySnapshot],
    outcomes: list[StoryChunks | BaseException],
) -> tuple[dict[int, set[UUID]], int, int, int, int, int]:
    desired: dict[int, set[UUID]] = {}
    failed = upserted = unchanged = generated = reused = 0
    for story, outcome in zip(stories, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            failed += 1
            logger.error(
                "Chunk generation failed for story_id=%s: %s",
                story["id"],
                outcome,
                exc_info=outcome,
            )
            continue
        if outcome.summary_failed:
            failed += 1
        generated += outcome.summary_generated
        reused += outcome.summary_reused
        try:
            with sessions.begin() as session:
                changed_rows = _upsert_chunks(session, outcome.rows)
                upserted += changed_rows
                unchanged += len(outcome.rows) - changed_rows
        except Exception:
            failed += 1
            logger.exception("Chunk persistence failed for story_id=%s", story["id"])
            continue
        desired[story["id"]] = {
            *(row["id"] for row in outcome.rows),
            *outcome.keep_ids,
        }
    return desired, failed, upserted, unchanged, generated, reused


async def _chunk_stories_locked(
    engine: Engine,
    client: AsyncOpenAI,
    limit: int | None,
    force: bool,
    untagged_delete_threshold: float,
) -> int:
    sessions = create_session_factory(engine)
    with sessions.begin() as session:
        removed_untagged = _remove_untagged_chunks(
            session, force=force, threshold=untagged_delete_threshold
        )

    last_id = 0
    processed = failed = generated_summaries = reused_summaries = 0
    upserted = unchanged = deleted_stale = 0
    semaphore = asyncio.Semaphore(SUMMARY_CONCURRENCY)
    while limit is None or processed < limit:
        batch_limit = (
            STORY_BATCH_SIZE
            if limit is None
            else min(STORY_BATCH_SIZE, limit - processed)
        )
        with sessions() as session:
            story_rows = list(
                session.execute(
                    select(
                        HNStory.id,
                        HNStory.title,
                        HNStory.url,
                        HNStory.text,
                        HNStory.author,
                        HNStory.points,
                        HNStory.num_comments,
                        HNStory.created_at,
                    )
                    .where(
                        HNStory.id > last_id,
                        HNStory.id.in_(select(StoryTech.story_id)),
                    )
                    .order_by(HNStory.id)
                    .limit(batch_limit)
                )
            )
            snapshots = _story_snapshots(session, story_rows)
            existing_summaries = _existing_summaries(
                session, [story["id"] for story in snapshots]
            )
        if not snapshots:
            break

        outcomes = await asyncio.gather(
            *(
                _chunks_for_story(
                    story,
                    client,
                    semaphore,
                    existing_summaries.get(story["id"]),
                )
                for story in snapshots
            ),
            return_exceptions=True,
        )
        desired, story_failures, story_upserted, story_unchanged, generated, reused = (
            _persist_story_outcomes(sessions, snapshots, outcomes)
        )
        failed += story_failures
        upserted += story_upserted
        unchanged += story_unchanged
        generated_summaries += generated
        reused_summaries += reused

        try:
            with sessions.begin() as session:
                deleted_stale += _delete_stale_chunks_batch(session, desired)
        except Exception:
            failed += 1
            logger.exception("Batch stale-chunk cleanup failed")

        processed += len(snapshots)
        last_id = snapshots[-1]["id"]
        logger.info("Processed %d stories in chunk stage", processed)

    logger.info(
        "Chunk stage complete: processed=%d failed=%d summaries_generated=%d "
        "summaries_reused=%d chunks_upserted=%d chunks_unchanged=%d chunks_deleted=%d "
        "untagged_chunks_deleted=%d",
        processed,
        failed,
        generated_summaries,
        reused_summaries,
        upserted,
        unchanged,
        deleted_stale,
        removed_untagged,
    )
    return processed


async def chunk_stories(
    engine: Engine,
    client: AsyncOpenAI,
    limit: int | None = None,
    force: bool = False,
    untagged_delete_threshold: float = UNTAGGED_DELETE_THRESHOLD,
) -> int:
    """Generate and reconcile chunks. Sync SQLAlchemy calls block this event loop."""
    with _try_advisory_lock(engine, CHUNK_LOCK_KEY):
        return await _chunk_stories_locked(
            engine, client, limit, force, untagged_delete_threshold
        )


def _pending_embedding_batches(
    sessions: sessionmaker[Session], limit: int | None
) -> Iterator[list[PendingChunk]]:
    attempted = 0
    cursor: tuple[datetime, UUID] | None = None
    active_version = embedding_version()
    while limit is None or attempted < limit:
        batch_limit = (
            CHUNK_BATCH_SIZE
            if limit is None
            else min(CHUNK_BATCH_SIZE, limit - attempted)
        )
        statement = select(
            HNChunk.id,
            HNChunk.content,
            HNChunk.content_hash,
            HNChunk.embedding_version,
            HNChunk.created_at,
        ).where(
            or_(
                HNChunk.embedding.is_(None),
                HNChunk.embedding_version.is_distinct_from(active_version),
            ),
            or_(
                HNChunk.embedding_attempts < MAX_EMBEDDING_ATTEMPTS,
                HNChunk.embedding_version.is_distinct_from(active_version),
            ),
        )
        if cursor is not None:
            statement = statement.where(
                tuple_(HNChunk.created_at, HNChunk.id) > tuple_(*cursor)
            )
        statement = statement.order_by(HNChunk.created_at, HNChunk.id).limit(
            batch_limit
        )
        with sessions() as session:
            rows = list(session.execute(statement))
        if not rows:
            return
        batch: list[PendingChunk] = [
            {
                "id": row.id,
                "content": row.content,
                "content_hash": row.content_hash,
                "embedding_version": row.embedding_version,
                "created_at": row.created_at,
            }
            for row in rows
        ]
        cursor = (batch[-1]["created_at"], batch[-1]["id"])
        attempted += len(batch)
        yield batch


def _validate_embedding_response(
    response: Any, expected_count: int
) -> dict[int, list[float]]:
    data = response.data
    if len(data) != expected_count:
        raise RuntimeError(
            f"Embedding response contained {len(data)} vectors; expected {expected_count}"
        )
    vectors: dict[int, list[float]] = {}
    for item in data:
        if item.index < 0 or item.index >= expected_count:
            raise RuntimeError(f"Embedding response index {item.index} is out of range")
        if item.index in vectors:
            raise RuntimeError(
                f"Embedding response contains duplicate index {item.index}"
            )
        if len(item.embedding) != settings.OPENAI_EMBEDDING_DIMENSIONS:
            raise RuntimeError(
                f"Embedding {item.index} has {len(item.embedding)} dimensions; expected "
                f"{settings.OPENAI_EMBEDDING_DIMENSIONS}"
            )
        vectors[item.index] = item.embedding
    if set(vectors) != set(range(expected_count)):
        raise RuntimeError("Embedding response indices are incomplete")
    return vectors


def _persist_embedding_batch(
    session: Session,
    batch: list[PendingChunk],
    vectors: dict[int, list[float]],
) -> int:
    if not batch:
        return 0
    rows = [
        {
            "id": chunk["id"],
            "content_hash": chunk["content_hash"],
            "embedding": vectors[index],
        }
        for index, chunk in enumerate(batch)
    ]
    new_embeddings = (
        values(
            column("id", HNChunk.id.type),
            column("content_hash", HNChunk.content_hash.type),
            column("expected_embedding_version", HNChunk.embedding_version.type),
            column("embedding", Vector(settings.OPENAI_EMBEDDING_DIMENSIONS)),
            name="new_embeddings",
        )
        .data(
            [
                (
                    row["id"],
                    row["content_hash"],
                    batch[index]["embedding_version"],
                    row["embedding"],
                )
                for index, row in enumerate(rows)
            ]
        )
        .alias("new_embeddings")
    )
    result = session.execute(
        update(HNChunk)
        .where(
            HNChunk.id == new_embeddings.c.id,
            HNChunk.content_hash == new_embeddings.c.content_hash,
            HNChunk.embedding_version.is_not_distinct_from(
                new_embeddings.c.expected_embedding_version
            ),
        )
        .values(
            embedding=new_embeddings.c.embedding,
            embedding_version=embedding_version(),
            embedding_error=None,
            embedding_attempts=0,
        )
    )
    return result.rowcount or 0


def _record_embedding_failure(
    session: Session, chunk: PendingChunk, error: Exception
) -> bool:
    result = session.execute(
        update(HNChunk)
        .where(
            HNChunk.id == chunk["id"],
            HNChunk.content_hash == chunk["content_hash"],
            HNChunk.embedding_version.is_not_distinct_from(chunk["embedding_version"]),
        )
        .values(
            embedding_attempts=HNChunk.embedding_attempts + 1,
            embedding_error=str(error)[:2_000],
            embedding=None,
            embedding_version=embedding_version(),
        )
    )
    return bool(result.rowcount)


def _is_chunk_specific_error(error: Exception) -> bool:
    return (
        isinstance(error, APIStatusError)
        and not isinstance(error, RateLimitError)
        and error.status_code in {400, 413, 422}
    )


async def _embed_isolated(
    client: AsyncOpenAI,
    batch: list[PendingChunk],
) -> tuple[
    list[tuple[PendingChunk, list[float]]], list[tuple[PendingChunk, Exception]]
]:
    inputs = [truncate_utf8(chunk["content"]) for chunk in batch]
    try:
        response = await create_with_retries(
            lambda inputs=inputs: client.with_options(max_retries=0).embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=inputs,
                dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS,
            )
        )
        vectors = _validate_embedding_response(response, len(batch))
        return (
            [(chunk, vectors[index]) for index, chunk in enumerate(batch)],
            [],
        )
    except Exception as error:
        if _is_transient(error) or not _is_chunk_specific_error(error):
            raise
        if len(batch) > 1:
            midpoint = len(batch) // 2
            left = await _embed_isolated(client, batch[:midpoint])
            right = await _embed_isolated(client, batch[midpoint:])
            return left[0] + right[0], left[1] + right[1]
        return [], [(batch[0], error)]


def _embed_request_batches(batch: list[PendingChunk]) -> Iterator[list[PendingChunk]]:
    current: list[PendingChunk] = []
    total_bytes = 0
    for chunk in batch:
        size = len(truncate_utf8(chunk["content"]).encode("utf-8"))
        if current and (
            len(current) >= CHUNK_BATCH_SIZE
            or total_bytes + size > MAX_EMBEDDING_BATCH_BYTES
        ):
            yield current
            current, total_bytes = [], 0
        current.append(chunk)
        total_bytes += size
    if current:
        yield current


async def _embed_pending_locked(
    engine: Engine, client: AsyncOpenAI, limit: int | None
) -> int:
    sessions = create_session_factory(engine)
    attempted = embedded = failed = batches = 0
    for page in _pending_embedding_batches(sessions, limit):
        for batch in _embed_request_batches(page):
            attempted += len(batch)
            batches += 1
            successful, failures = await _embed_isolated(client, batch)
            with sessions.begin() as session:
                embedded += _persist_embedding_batch(
                    session,
                    [item for item, _ in successful],
                    {index: vector for index, (_, vector) in enumerate(successful)},
                )
                for chunk, error in failures:
                    failed += _record_embedding_failure(session, chunk, error)
            logger.info(
                "Embedding batch complete: batch=%d embedded=%d failures=%d",
                batches,
                len(successful),
                len(failures),
            )
    logger.info(
        "Embedding stage complete: attempted=%d embedded=%d failures=%d batches=%d",
        attempted,
        embedded,
        failed,
        batches,
    )
    return embedded


async def embed_pending(
    engine: Engine,
    client: AsyncOpenAI,
    limit: int | None = None,
) -> int:
    """Embed pending chunks; `limit` counts attempted chunks, not successes.

    Sync SQLAlchemy calls remain on the event loop; use a dedicated worker thread
    or async engine if this stage moves into latency-sensitive request handling.
    """
    with _try_advisory_lock(engine, EMBED_LOCK_KEY):
        return await _embed_pending_locked(engine, client, limit)
