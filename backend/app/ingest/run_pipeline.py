import argparse
import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from openai import AsyncOpenAI

from app.config import settings
from app.database.session import create_database_engine, create_session_factory
from app.ingest.chunk_and_embed import chunk_stories, embed_pending
from app.ingest.load_raw import DEFAULT_INPUT, load_raw
from app.ingest.tag_stories import (
    create_tagger,
    refresh_trend_view,
    sync_monthly_totals,
    tag_stories,
)

logger = logging.getLogger("techpilot.ingest")
DAILY_PIPELINE_ADVISORY_LOCK_KEY = 741_203_119


def run_daily_pipeline(
    engine, crawl: Callable[[], object], limit: int | None = None
) -> int:
    """Run crawl, tagging, and aggregate refresh under a nonblocking database lock."""
    with engine.connect() as lock_connection:
        acquired = lock_connection.exec_driver_sql(
            "SELECT pg_try_advisory_lock(%s)", (DAILY_PIPELINE_ADVISORY_LOCK_KEY,)
        ).scalar_one()
        if not acquired:
            logger.warning(
                "Daily HN pipeline skipped: another run holds the advisory lock"
            )
            return 0
        try:
            crawl()
            tagged = tag_stories(engine, limit=limit, refresh_view=False)
            if limit is None:
                sessions = create_session_factory(engine)
                sync_monthly_totals(sessions)
                refresh_trend_view(engine)
            return tagged
        finally:
            lock_connection.exec_driver_sql(
                "SELECT pg_advisory_unlock(%s)", (DAILY_PIPELINE_ADVISORY_LOCK_KEY,)
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Hacker News corpus pipeline.")
    parser.add_argument(
        "--all", action="store_true", help="run load, tag, chunk, and embed"
    )
    parser.add_argument(
        "--load", action="store_true", help="load raw downloaded threads"
    )
    parser.add_argument(
        "--tag", action="store_true", help="tag stories and refresh trends"
    )
    parser.add_argument(
        "--chunk",
        action="store_true",
        help="generate title, summary, and comment chunks",
    )
    parser.add_argument(
        "--embed", action="store_true", help="embed chunks whose vectors are null"
    )
    parser.add_argument(
        "--refresh-trends",
        action="store_true",
        help="concurrently refresh trend_monthly",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="maximum records per selected stage; 0 means no limit",
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT, help="raw HN payload root"
    )
    parser.add_argument("--dictionary", type=Path, help="override data/tech_dict.yaml")
    return parser


async def run_pipeline(args: argparse.Namespace) -> None:
    if args.all:
        args.load = args.tag = args.chunk = args.embed = True
    if not any((args.load, args.tag, args.chunk, args.embed, args.refresh_trends)):
        raise SystemExit("select --all or at least one pipeline stage")
    if args.limit < 0:
        raise SystemExit("--limit must be zero or greater")

    engine = create_database_engine()
    sessions = create_session_factory(engine)
    try:
        if args.load:
            loaded, comments, skipped = load_raw(
                sessions, args.input, args.limit or None
            )
            logger.info(
                "Load stage: stories=%d comments=%d skipped_files=%d",
                loaded,
                comments,
                skipped,
            )
        if args.tag:
            tagged = tag_stories(
                engine,
                tagger=create_tagger(args.dictionary),
                limit=args.limit or None,
            )
            logger.info("Tag stage: stories=%d", tagged)
        elif args.refresh_trends:
            from app.ingest.tag_stories import refresh_trend_view

            refresh_trend_view(engine)
            logger.info("Trend view refreshed")

        if args.chunk or args.embed:
            async with AsyncOpenAI(api_key=settings.OPENAI_API_KEY) as client:
                if args.chunk:
                    generated = await chunk_stories(engine, client, args.limit or None)
                    logger.info("Chunk stage: stories=%d", generated)
                if args.embed:
                    embedded = await embed_pending(engine, client, args.limit or None)
                    logger.info("Embed stage: chunks=%d", embedded)
    finally:
        engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
