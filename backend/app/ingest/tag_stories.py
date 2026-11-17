import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Date, cast, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import HNStory, HNTotalsMonthly, StoryTech
from app.database.session import create_session_factory

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.tagger import Tagger

logger = logging.getLogger(__name__)
TAG_BATCH_SIZE = 500


def create_tagger(dictionary: Path | None = None) -> Tagger:
    path = dictionary or PROJECT_ROOT / "data" / "tech_dict.yaml"
    return Tagger(path, strict=True)


def _via_for_story(
    tech: str,
    title_matches: dict[str, Any],
    body_matches: dict[str, Any],
) -> str | None:
    matches: list[str] = []
    for source in (title_matches, body_matches):
        match = source.get(tech)
        if match is not None:
            matches.append(match.via)
    if "alias" in matches:
        return "direct"
    # Tagger's `context` means a context-dependent alias was confirmed. The
    # persisted story_tech vocabulary calls this `ambiguous`.
    if "context" in matches:
        return "ambiguous"
    if "implied" in matches:
        return "implied"
    return None


def story_tag_rows(tagger: Tagger, story: HNStory) -> list[dict[str, Any]]:
    title = story.title or ""
    body = story.text or ""
    url = story.url or ""
    found = tagger.tag_story(title, body, url)
    title_matches = tagger.tag(
        title,
        context_text=f"{url}\n{body[:300]}",
    )
    body_matches = tagger.tag(
        body,
        context_text=f"{title}\n{url}",
    )
    rows: list[dict[str, Any]] = []
    for tech, result in sorted(found.items()):
        via = _via_for_story(tech, title_matches, body_matches)
        if via is None:
            logger.warning(
                "Could not determine match provenance for story=%s tech=%s; "
                "using ambiguous",
                story.id,
                tech,
            )
            via = "ambiguous"
        rows.append(
            {
                "story_id": story.id,
                "tech": tech,
                "category": tagger.category[tech],
                "in_title": result["in_title"],
                "via": via,
            }
        )
    return rows


def sync_monthly_totals(sessions: sessionmaker[Session]) -> int:
    month = cast(
        func.date_trunc("month", func.timezone("UTC", HNStory.created_at)), Date
    )
    with sessions() as session:
        totals = (
            session.execute(
                select(
                    month.label("month"),
                    func.count(HNStory.id).label("total_stories"),
                    func.coalesce(func.sum(HNStory.points), 0).label("total_points"),
                    func.coalesce(func.sum(HNStory.num_comments), 0).label(
                        "total_comments"
                    ),
                    literal("corpus", type_=HNTotalsMonthly.source.type).label(
                        "source"
                    ),
                ).group_by(month)
            )
            .mappings()
            .all()
        )

    if not totals:
        return 0
    rows = [dict(row) for row in totals]
    statement = insert(HNTotalsMonthly).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[HNTotalsMonthly.month],
        set_={
            "total_stories": statement.excluded.total_stories,
            "total_points": statement.excluded.total_points,
            "total_comments": statement.excluded.total_comments,
            "source": statement.excluded.source,
        },
        where=HNTotalsMonthly.source == "corpus",
    )
    with sessions.begin() as session:
        result = session.execute(statement)
    return result.rowcount or 0


def refresh_trend_view(engine: Engine) -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        index_definition = connection.exec_driver_sql(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'trend_monthly'
              AND indexname = 'uq_trend_monthly_tech_category_month'
            """
        ).scalar_one_or_none()
        if not index_definition or "unique index" not in index_definition.lower():
            raise RuntimeError(
                "Concurrent trend refresh requires the unique index "
                "uq_trend_monthly_tech_category_month"
            )
        connection.exec_driver_sql(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY trend_monthly"
        )


def tag_stories(
    engine: Engine,
    tagger: Tagger | None = None,
    limit: int | None = None,
    batch_size: int = TAG_BATCH_SIZE,
    refresh_view: bool | None = None,
) -> int:
    active_tagger = tagger or create_tagger()
    sessions = create_session_factory(engine)
    last_id = 0
    processed = 0
    while limit is None or processed < limit:
        batch_limit = (
            batch_size if limit is None else min(batch_size, limit - processed)
        )
        with sessions() as session:
            stories = list(
                session.scalars(
                    select(HNStory)
                    .where(HNStory.id > last_id)
                    .order_by(HNStory.id)
                    .limit(batch_limit)
                )
            )
        if not stories:
            break

        story_ids = [story.id for story in stories]
        tag_rows = [
            row for story in stories for row in story_tag_rows(active_tagger, story)
        ]
        with sessions.begin() as session:
            session.execute(delete(StoryTech).where(StoryTech.story_id.in_(story_ids)))
            if tag_rows:
                statement = insert(StoryTech).values(tag_rows)
                statement = statement.on_conflict_do_update(
                    index_elements=[StoryTech.story_id, StoryTech.tech],
                    set_={
                        "category": statement.excluded.category,
                        "in_title": statement.excluded.in_title,
                        "via": statement.excluded.via,
                    },
                )
                session.execute(statement)
        processed += len(stories)
        last_id = stories[-1].id
        logger.info("Tagged %d stories", processed)

    should_refresh = limit is None if refresh_view is None else refresh_view
    sync_monthly_totals(sessions)
    if should_refresh:
        refresh_trend_view(engine)
    logger.info(
        "Tagging complete: stories=%d dictionary=%s",
        processed,
        active_tagger.dict_version,
    )
    return processed
