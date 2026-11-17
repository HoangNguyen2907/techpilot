from sqlalchemy.dialects import postgresql

from app.ingest.load_raw import upsert_story


class _Session:
    statement = None

    def execute(self, statement: object) -> None:
        self.statement = statement


def test_story_upsert_resets_tag_version_only_for_content_changes() -> None:
    session = _Session()

    upsert_story(
        session,  # type: ignore[arg-type]
        {
            "id": 1,
            "title": "Updated title",
            "text": None,
            "url": "https://example.com",
            "author": "author",
            "points": 12,
            "num_comments": 4,
            "created_at": None,
            "kind": "story",
        },
    )

    compiled = session.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "IS DISTINCT FROM excluded.title" in sql
    assert "IS DISTINCT FROM excluded.text" in sql
    assert "IS DISTINCT FROM excluded.url" in sql
    assert "CASE WHEN" in sql
    assert "tagged_dict_version" in sql
