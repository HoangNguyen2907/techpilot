import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Self
from uuid import uuid4

import pytest
from openai import BadRequestError
from sqlalchemy.dialects import postgresql

from app.database.models import HNChunk
from app.ingest import chunk_and_embed as pipeline


def _story(**changes: Any) -> pipeline.StorySnapshot:
    story: pipeline.StorySnapshot = {
        "id": 123,
        "title": "Build with Bun",
        "url": "https://example.com/bun",
        "text": "A short story body.",
        "author": "writer",
        "points": 50,
        "num_comments": 60,
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
        "technologies": [{"tech": "bun", "category": "runtime"}],
        "comments": [],
    }
    story.update(changes)
    return story


def test_chunk_id_stability_and_parts() -> None:
    base = pipeline.chunk_id("comment", 123, 456)
    assert base == pipeline.chunk_id("comment", 123, 456)
    assert base == pipeline.chunk_id("comment", 123, 456, 0)
    assert base != pipeline.chunk_id("title", 123)
    assert base != pipeline.chunk_id("comment", 123, 457)
    assert base != pipeline.chunk_id("comment", 123, 456, 1)


def test_truncate_utf8_preserves_valid_text_within_byte_limit() -> None:
    result = pipeline.truncate_utf8("🙂漢字abc", 7)
    assert result.encode("utf-8").decode("utf-8") == result
    assert len(result.encode("utf-8")) <= 7
    assert result == "🙂漢"


@pytest.mark.parametrize(
    ("indices", "dimensions", "expected_count", "error"),
    [
        ([0], 1536, 2, "contained 1 vectors"),
        ([0, 0], 1536, 2, "duplicate index"),
        ([0, 2], 1536, 2, "out of range"),
        ([0, 1], 2, 2, "dimensions"),
    ],
)
def test_validate_embedding_response_rejects_bad_vectors(
    indices: list[int], dimensions: int, expected_count: int, error: str
) -> None:
    response = SimpleNamespace(
        data=[
            SimpleNamespace(index=index, embedding=[0.0] * dimensions)
            for index in indices
        ]
    )
    with pytest.raises(RuntimeError, match=error):
        pipeline._validate_embedding_response(response, expected_count)


class _Result:
    def __init__(self, rowcount: int = 0) -> None:
        self.rowcount = rowcount


class _RecordingSession:
    def __init__(self, fail_story_id: int | None = None) -> None:
        self.statements: list[str] = []
        self.fail_story_id = fail_story_id

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: Any) -> _Result:
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.statements.append(sql)
        if self.fail_story_id and self.fail_story_id in compiled.params.values():
            raise RuntimeError("database write failed")
        return _Result(rowcount=1)

    def scalar(self, statement: Any) -> int:
        return 100


class _SessionFactory:
    def __init__(self, fail_story_id: int | None = None) -> None:
        self.session = _RecordingSession(fail_story_id)
        self.transactions = 0

    def __call__(self) -> _RecordingSession:
        return self.session

    def begin(self) -> _RecordingSession:
        self.transactions += 1
        return self.session


def test_upsert_uses_distinct_guards_and_embedding_version() -> None:
    session = _RecordingSession()
    story = _story()
    row = pipeline.chunk_row(story, "title", "stable content", 0.5)
    pipeline._upsert_chunks(session, [row])
    sql = session.statements[0]
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert "IS DISTINCT FROM" in sql
    assert "CASE WHEN" in sql
    assert "hn_chunks.content_hash = excluded.content_hash" in sql
    assert "embedding_version" in sql


def test_batch_reconcile_deletes_only_chunks_not_desired() -> None:
    session = _RecordingSession()
    deleted = pipeline._delete_stale_chunks_batch(
        session, {123: {pipeline.chunk_id("title", 123)}}
    )
    assert deleted == 1
    assert session.statements[0].lstrip().startswith("DELETE FROM hn_chunks")
    assert "NOT (hn_chunks.story_id" in session.statements[0]


def test_matching_summary_reused_without_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    story = _story()
    source_hash = pipeline._summary_source_hash(story)
    existing_id = pipeline.chunk_id("summary", story["id"])

    async def unexpected_call(*_: Any, **__: Any) -> str:
        raise AssertionError("summary should be reused")

    monkeypatch.setattr(pipeline, "summarize_thread", unexpected_call)
    outcome = asyncio.run(
        pipeline._chunks_for_story(
            story,
            SimpleNamespace(),
            asyncio.Semaphore(1),
            (existing_id, "old summary", {"source_hash": source_hash}),
        )
    )
    assert outcome.summary_reused == 1
    assert outcome.keep_ids == {existing_id}
    assert all(row["chunk_type"] != "summary" for row in outcome.rows)


def test_changed_summary_source_regenerates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    story = _story()
    calls = 0

    async def summarize(*_: Any, **__: Any) -> str:
        nonlocal calls
        calls += 1
        return "fresh synthesis"

    monkeypatch.setattr(pipeline, "summarize_thread", summarize)
    outcome = asyncio.run(
        pipeline._chunks_for_story(
            story,
            SimpleNamespace(),
            asyncio.Semaphore(1),
            (pipeline.chunk_id("summary", story["id"]), "old", {"source_hash": "old"}),
        )
    )
    summary = next(row for row in outcome.rows if row["chunk_type"] == "summary")
    assert calls == 1
    assert outcome.summary_generated == 1
    assert summary["metadata_json"]["source_hash"] == pipeline._summary_source_hash(
        story
    )


def test_summary_failure_keeps_old_summary_and_other_story_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    story_one = _story()
    story_two = _story(id=124, title="Another story")
    old_summary_id = pipeline.chunk_id("summary", story_one["id"])

    async def fail(*_: Any, **__: Any) -> str:
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(pipeline, "summarize_thread", fail)
    failed_outcome = asyncio.run(
        pipeline._chunks_for_story(
            story_one,
            SimpleNamespace(),
            asyncio.Semaphore(1),
            (old_summary_id, "old summary", {"source_hash": "old"}),
        )
    )
    successful_outcome = pipeline.StoryChunks(
        [pipeline.chunk_row(story_two, "title", "title", 0.5)],
        set(),
    )
    sessions = _SessionFactory()
    desired, failures, _, _, _, _ = pipeline._persist_story_outcomes(
        sessions,
        [story_one, story_two],
        [failed_outcome, successful_outcome],
    )
    assert failures == 1
    assert old_summary_id in desired[story_one["id"]]
    assert story_two["id"] in desired
    assert sessions.transactions == 2


def test_story_database_error_does_not_rollback_other_story() -> None:
    first, second = _story(), _story(id=124, title="Second")
    outcomes = [
        pipeline.StoryChunks(
            [pipeline.chunk_row(first, "title", "one", 0.5)],
            set(),
        ),
        pipeline.StoryChunks(
            [pipeline.chunk_row(second, "title", "two", 0.5)],
            set(),
        ),
    ]
    sessions = _SessionFactory(fail_story_id=first["id"])

    desired, failures, _, _, _, _ = pipeline._persist_story_outcomes(
        sessions, [first, second], outcomes
    )

    assert failures == 1
    assert first["id"] not in desired
    assert second["id"] in desired
    assert sessions.transactions == 2


class _PagedSessionFactory:
    def __init__(self, rows: list[Any]) -> None:
        self.pages = [rows[:50], rows[50:]]
        self.sql: list[str] = []

    def __call__(self) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: Any) -> list[Any]:
        self.sql.append(str(statement.compile(dialect=postgresql.dialect())))
        return self.pages.pop(0) if self.pages else []


def test_pending_chunks_use_keyset_without_unbounded_id_list() -> None:
    created = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            id=uuid4(),
            content="text",
            content_hash="hash",
            embedding_version=None,
            created_at=created,
        )
        for _ in range(51)
    ]
    sessions = _PagedSessionFactory(rows)
    batches = list(pipeline._pending_embedding_batches(sessions, None))  # type: ignore[arg-type]
    assert [len(batch) for batch in batches] == [50, 1]
    assert all("NOT IN" not in sql for sql in sessions.sql)
    assert "created_at" in sessions.sql[1] and " > " in sessions.sql[1]
    assert "embedding_attempts" in sessions.sql[0]


class _PoisonClient:
    def __init__(self, poison: str) -> None:
        self.poison = poison
        self.embeddings = self

    def with_options(self, **_: Any) -> Self:
        return self

    async def create(self, *, input: list[str], **_: Any) -> Any:
        if self.poison in input:
            response = SimpleNamespace(
                status_code=400, headers={}, request=SimpleNamespace()
            )
            raise BadRequestError("invalid input", response=response, body=None)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[0.0] * 1536)
                for index, _ in enumerate(input)
            ]
        )


def test_poison_embedding_isolated_while_other_chunks_succeed() -> None:
    chunks: list[pipeline.PendingChunk] = [
        {
            "id": uuid4(),
            "content": value,
            "content_hash": pipeline.content_hash(value),
            "embedding_version": None,
            "created_at": datetime(2025, 1, 1, tzinfo=UTC),
        }
        for value in ("good one", "poison", "good two")
    ]
    successful, failed = asyncio.run(
        pipeline._embed_isolated(_PoisonClient("poison"), chunks)  # type: ignore[arg-type]
    )
    assert {chunk["content"] for chunk, _ in successful} == {"good one", "good two"}
    assert [chunk["content"] for chunk, _ in failed] == ["poison"]


def test_embedding_failure_record_uses_content_hash_compare_and_swap() -> None:
    session = _RecordingSession()
    content = "current"
    chunk: pipeline.PendingChunk = {
        "id": uuid4(),
        "content": content,
        "content_hash": pipeline.content_hash(content),
        "embedding_version": None,
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
    }
    pipeline._record_embedding_failure(session, chunk, RuntimeError("bad chunk"))
    sql = session.statements[0]
    assert "embedding_attempts=(hn_chunks.embedding_attempts +" in sql
    assert "hn_chunks.content_hash =" in sql


def test_untagged_cleanup_refuses_large_deletion_and_allows_small_deletion() -> None:
    class CountsSession(_RecordingSession):
        def __init__(self, counts: list[int]) -> None:
            super().__init__()
            self.counts = iter(counts)

        def scalar(self, statement: Any) -> int:
            return next(self.counts)

    with pytest.raises(RuntimeError, match="force=True"):
        pipeline._remove_untagged_chunks(CountsSession([100, 21]))  # type: ignore[arg-type]
    session = CountsSession([100, 20])
    assert pipeline._remove_untagged_chunks(session) == 1  # type: ignore[arg-type]


def test_title_content_excludes_volatile_fields_and_metadata_keeps_them() -> None:
    story = _story()
    row = pipeline._story_chunks(story)[0]
    assert "Points:" not in row["content"]
    assert "Comments:" not in row["content"]
    assert "2025-01-01" not in row["content"]
    assert row["metadata_json"]["points"] == 50
    assert row["metadata_json"]["num_comments"] == 60
    assert row["metadata_json"]["created_at"].startswith("2025-01-01")


def test_long_comment_is_split_and_short_reply_has_parent_context() -> None:
    story = _story(
        comments=[
            {
                "id": 456,
                "parent_id": 455,
                "parent_text": "A detailed parent point.",
                "author": "commenter",
                "quality_score": 0.8,
                "text": "🙂" * 4_000,
            },
            {
                "id": 457,
                "parent_id": 455,
                "parent_text": "A detailed parent point.",
                "author": "replyer",
                "quality_score": 0.8,
                "text": "A short but useful reply.",
            },
        ]
    )
    chunks = [
        row for row in pipeline._story_chunks(story) if row["chunk_type"] == "comment"
    ]
    first_id = pipeline.chunk_id("comment", story["id"], 456)

    assert len(chunks) > 1
    assert chunks[0]["id"] == first_id
    assert all(len(row["content"].encode("utf-8")) <= 6_000 for row in chunks)
    reply = next(row for row in chunks if row["comment_id"] == 457)
    assert reply["metadata_json"]["parent_id"] == 455
    assert "Parent comment context:" in reply["content"]


def test_stale_embedding_update_uses_content_hash_guard() -> None:
    session = _RecordingSession()
    content = "before request"
    chunk: pipeline.PendingChunk = {
        "id": uuid4(),
        "content": content,
        "content_hash": pipeline.content_hash(content),
        "embedding_version": None,
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
    }
    pipeline._persist_embedding_batch(session, [chunk], {0: [0.0] * 1536})
    sql = session.statements[0]
    assert "FROM (VALUES" in sql
    assert "hn_chunks.content_hash = new_embeddings.content_hash" in sql
    assert HNChunk.embedding_attempts.key in sql
