from datetime import UTC, datetime
from typing import Self

import pytest
from sqlalchemy import Date, cast, func, select
from sqlalchemy.dialects import postgresql

from app.database.models import HNStory, HNTotalsMonthly
from app.ingest.tag_stories import Tagger, story_tag_rows, sync_monthly_totals


@pytest.mark.parametrize(
    ("title", "body", "expected_via"),
    [("ReactJS app", "", "direct"), ("Go module", "", "ambiguous")],
)
def test_story_tag_rows_returns_taggers_match_provenance(
    title: str, body: str, expected_via: str
) -> None:
    from pathlib import Path

    tagger = Tagger(Path(__file__).resolve().parents[3] / "data" / "tech_dict.yaml")
    story = HNStory(id=1, title=title, text=body, url=None)

    rows = story_tag_rows(tagger, story)

    assert rows
    assert rows[0]["via"] == expected_via


def test_month_expression_uses_utc_for_timestamptz_boundaries() -> None:
    month = cast(
        func.date_trunc("month", func.timezone("UTC", HNStory.created_at)), Date
    )
    compiled = select(month).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "date_trunc(" in sql
    assert "timezone(" in sql
    assert "hn_stories.created_at" in sql
    assert "month" in compiled.params.values()
    assert "UTC" in compiled.params.values()

    before_utc_boundary = datetime(2025, 1, 31, 23, 59, tzinfo=UTC)
    at_utc_boundary = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)
    assert before_utc_boundary.month == 1
    assert at_utc_boundary.month == 2


class _Result:
    rowcount = 1

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, object]]:
        return [
            {
                "month": datetime(2025, 1, 1, tzinfo=UTC).date(),
                "total_stories": 10,
                "total_points": 100,
                "total_comments": 50,
                "source": "corpus",
            }
        ]


class _Session:
    def __init__(self) -> None:
        self.upsert_sql = ""
        self.upsert_params: dict[str, object] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: object) -> _Result:
        compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
        sql = str(compiled)
        if sql.lstrip().upper().startswith("INSERT"):
            self.upsert_sql = sql
            self.upsert_params = compiled.params
        return _Result()


class _Sessions:
    def __init__(self) -> None:
        self.session = _Session()

    def __call__(self) -> _Session:
        return self.session

    def begin(self) -> _Session:
        return self.session


def test_sync_monthly_totals_only_updates_corpus_rows_and_returns_rowcount() -> None:
    sessions = _Sessions()

    changed = sync_monthly_totals(sessions)  # type: ignore[arg-type]

    assert changed == 1
    assert "ON CONFLICT (month) DO UPDATE" in sessions.session.upsert_sql
    assert (
        "WHERE hn_totals_monthly.source = %(source_1)s" in sessions.session.upsert_sql
    )
    assert sessions.session.upsert_params["source_1"] == "corpus"
    assert "corpus" in sessions.session.upsert_params.values()
    assert HNTotalsMonthly.source.default.arg == "bigquery"
