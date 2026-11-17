import html
import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import HNComment, HNStory

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "downloads" / "hn"
_TAG = re.compile(r"</?[^>]+>")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200d\u2060\ufeff\u00ad]")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_CODE = re.compile(r"<\s*(?:pre|code)\b", re.IGNORECASE)
_VERSION = re.compile(r"\b(?:v(?:ersion)?\s*)?\d+(?:\.\d+){1,3}\b", re.IGNORECASE)


def clean_hn_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"(?i)<\s*br\s*/?\s*>|</\s*(?:p|div|pre|li)\s*>", "\n", value)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _ZERO_WIDTH.sub("", text)
    text = _URL.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_created_at(
    item: dict[str, Any], fallback: datetime | None = None
) -> datetime:
    timestamp = item.get("created_at_i")
    if timestamp is not None:
        return datetime.fromtimestamp(int(timestamp), UTC)
    value = item.get("created_at")
    if value:
        created_at = datetime.fromisoformat(str(value))
        return (
            created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at
        )
    if fallback is not None:
        return fallback
    raise ValueError("item has no created_at_i or created_at")


def story_kind(title: str) -> str:
    normalized = title.lstrip().lower()
    if normalized.startswith("ask hn:"):
        return "ask_hn"
    if normalized.startswith("show hn:"):
        return "show_hn"
    return "story"


def comment_quality_score(
    raw_text: str, clean_text: str, n_children: int, depth: int
) -> float:
    length_score = min(len(clean_text) / 300, 1.0) * 0.65
    code_bonus = 0.15 if _CODE.search(raw_text) or "```" in raw_text else 0.0
    version_bonus = 0.1 if _VERSION.search(clean_text) else 0.0
    traction_bonus = min(n_children / 10, 1.0) * 0.1
    depth_penalty = min(max(depth - 4, 0) * 0.02, 0.1)
    return round(
        max(
            0.0,
            min(1.0, length_score + code_bonus + version_bonus + traction_bonus)
            - depth_penalty,
        ),
        4,
    )


def flatten_comments(
    story_id: int,
    children: list[dict[str, Any]],
    story_created_at: datetime,
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    stack: list[tuple[dict[str, Any], int | None, int | None, int]] = [
        (child, None, None, 0)
        for child in reversed([item for item in children if isinstance(item, dict)])
    ]
    while stack:
        item, parent_id, parent_index, depth = stack.pop()
        raw_id = item.get("id")
        try:
            comment_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            comment_id = None
        raw_text = str(item.get("text") or "")
        cleaned = clean_hn_text(raw_text)
        row = None
        if comment_id is not None and cleaned:
            row = {
                "id": comment_id,
                "story_id": story_id,
                "parent_id": parent_id,
                "depth": depth,
                "author": item.get("author"),
                "text": cleaned,
                "n_children": 0,
                "quality_score": 0.0,
                "created_at": parse_created_at(item, story_created_at),
            }
        node_index = len(nodes)
        nodes.append(
            {
                "id": comment_id,
                "parent_index": parent_index,
                "row": row,
                "raw_text": raw_text,
                "depth": depth,
            }
        )
        child_parent_id = comment_id if comment_id is not None else parent_id
        nested = item.get("children") or []
        for child in reversed([item for item in nested if isinstance(item, dict)]):
            stack.append((child, child_parent_id, node_index, depth + 1))

    descendants = [0] * len(nodes)
    flattened: list[dict[str, Any]] = []
    for index in range(len(nodes) - 1, -1, -1):
        node = nodes[index]
        row = node["row"]
        if row is not None:
            row["n_children"] = descendants[index]
            row["quality_score"] = comment_quality_score(
                node["raw_text"], row["text"], row["n_children"], node["depth"]
            )
            flattened.append(row)
        parent_index = node["parent_index"]
        if parent_index is not None:
            descendants[parent_index] += descendants[index] + 1
    flattened.reverse()
    return flattened


def story_row(item: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_id = item.get("id", item.get("objectID"))
    if raw_id is None:
        raise ValueError("story has no id")
    story_id = int(raw_id)
    title = clean_hn_text(str(item.get("title") or ""))
    if not title:
        raise ValueError(f"story {story_id} has no title")
    created_at = parse_created_at(item)
    children = item.get("children") or []
    story = {
        "id": story_id,
        "title": title,
        "url": item.get("url"),
        "text": clean_hn_text(item.get("text")),
        "author": item.get("author"),
        "points": int(item.get("points") or 0),
        "num_comments": int(item.get("num_comments") or 0),
        "created_at": created_at,
        "kind": story_kind(title),
    }
    return story, flatten_comments(story_id, children, created_at)


def upsert_story(session: Session, row: dict[str, Any]) -> None:
    statement = insert(HNStory).values(row)
    statement = statement.on_conflict_do_update(
        index_elements=[HNStory.id],
        set_={key: getattr(statement.excluded, key) for key in row if key != "id"},
    )
    session.execute(statement)


def upsert_comments(session: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    statement = insert(HNComment).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[HNComment.id],
        set_={key: getattr(statement.excluded, key) for key in rows[0] if key != "id"},
    )
    session.execute(statement)


def raw_files(root: Path = DEFAULT_INPUT, limit: int | None = None) -> Iterator[Path]:
    if not root.exists():
        return
    for yielded, path in enumerate(root.rglob("*.json"), start=1):
        yield path
        if limit is not None and yielded >= limit:
            return


def load_raw(
    sessions: sessionmaker[Session],
    root: Path = DEFAULT_INPUT,
    limit: int | None = None,
) -> tuple[int, int, int]:
    stories_loaded = comments_loaded = files_skipped = 0
    for path in raw_files(root, limit):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            story, comments = story_row(item)
            with sessions.begin() as session:
                upsert_story(session, story)
                upsert_comments(session, comments)
            stories_loaded += 1
            comments_loaded += len(comments)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            files_skipped += 1
            logger.warning("Skipping invalid HN payload %s: %s", path, exc)
    logger.info(
        "Raw load complete: stories=%d comments=%d skipped_files=%d",
        stories_loaded,
        comments_loaded,
        files_skipped,
    )
    return stories_loaded, comments_loaded, files_skipped
