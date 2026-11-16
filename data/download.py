"""Download full Hacker News threads (story + nested comment tree) from the Algolia HN API.

Output: data/downloads/hn/<year>/<story_id>.json, one file per story, where <year> is the
year the story was created (UTC). The file is the raw Algolia /items/{id} payload plus one
extra key, `fetched_at` (ISO 8601 UTC), so a run can be reproduced and stale threads found.

Which stories (pick one):
    -q KEYWORD          keywords you type (repeat for several)
    --from-dict [PATH]  keywords taken from tech_dict.yaml (default: next to this script),
                        optionally limited with --categories mobile,state-mgmt
    --all               no keyword: every story above --min-points

Examples:
    # all technologies in tech_dict.yaml with at least 100 points
    uv run data/download_hn.py --from-dict --since 2021 --min-points 100

    # only mobile and state-management technologies
    uv run data/download_hn.py --from-dict --categories mobile,state-mgmt --since 2021

    # first test: 5 threads
    uv run data/download_hn.py -q flutter -q riverpod --since 2021 --min-points 20 --limit 5

    # daily incremental: stories since a date
    uv run data/download_hn.py -q flutter -q dart --since 2026-11-15

    # weekly: also re-download threads younger than 7 days (comments are still arriving)
    uv run data/download_hn.py -q flutter --since 2026-11-01 --refresh-days 7

Notes:
  * Re-running is safe: threads already on disk are skipped (unless --refresh-days applies).
  * Keyword search matches title, text and URL, so ambiguous keywords ("go", "swift") bring
    noise. Filter during tagging.
  * Algolia returns at most 1000 hits per query. The time range is split in halves until each
    slice fits, so long ranges are not silently truncated.
  * Be polite: requests are spaced by --delay seconds (default 0.4 s, about 9000 requests/hour,
    under Algolia's public limit of 10,000/hour per IP) and retried with backoff on 429/5xx.
  * Review the current Hacker News / Algolia terms before any commercial use.

Only pyyaml is needed, and only for --from-dict (uv installs it from the header above).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://hn.algolia.com/api/v1"
DEFAULT_OUT = Path(__file__).resolve().parent / "downloads" / "hn"
DEFAULT_DICT = Path(__file__).resolve().parent / "tech_dict.yaml"
MIN_KEYWORD_LEN = 3
SAFE_KEYWORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
MAX_HITS = 1000  # hard cap Algolia applies to one query
PAGE_SIZE = 100
MIN_SLICE_SECONDS = 3600  # do not split a window below one hour
USER_AGENT = "hn-tech-copilot-downloader/1.0"
RETRY_STATUS = {429, 500, 502, 503, 504}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- http
class Client:
    """Tiny JSON-over-HTTP client with rate limiting and retries."""

    def __init__(self, delay: float = 0.4, retries: int = 5):
        self.delay = delay
        self.retries = retries
        self._last = 0.0

    def _throttle(self) -> None:
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get_json(self, path: str, params: dict | None = None):
        """GET API/path. Returns parsed JSON, or None on 404."""
        url = f"{API}/{path}" + (f"?{urlencode(params)}" if params else "")
        for attempt in range(self.retries + 1):
            self._throttle()
            wait = 2.0 * 2**attempt  # 2, 4, 8, 16, 32, 64 s
            try:
                req = Request(
                    url,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                )
                with urlopen(req, timeout=60) as resp:
                    return json.load(resp)
            except HTTPError as e:
                if e.code == 404:
                    return None
                if e.code not in RETRY_STATUS or attempt == self.retries:
                    raise
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if retry_after and retry_after.isdigit():
                    wait = max(wait, float(retry_after))
                reason = f"HTTP {e.code}"
            except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                if attempt == self.retries:
                    raise
                reason = type(e).__name__
            log(f"  retry {attempt + 1}/{self.retries} in {wait:.0f}s ({reason})")
            time.sleep(wait)
        raise RuntimeError("unreachable")


# ------------------------------------------------------------------------- search
def search_window(
    client: Client, query: str, start: int, end: int, min_points: int
) -> Iterator[dict]:
    """Yield story hits for `query` with start <= created_at_i < end.

    Splits the window in halves when Algolia reports more hits than it will return.
    """
    numeric = [f"created_at_i>={start}", f"created_at_i<{end}"]
    if min_points:
        numeric.append(f"points>={min_points}")
    params = {
        "query": query,
        "tags": "story",  # includes Ask HN and Show HN
        "numericFilters": ",".join(numeric),
        "hitsPerPage": PAGE_SIZE,
        "page": 0,
        "attributesToRetrieve": "objectID,created_at_i,title,points,num_comments",
    }
    page = client.get_json("search_by_date", params)
    if not page or not page.get("nbHits"):
        return
    if page["nbHits"] > MAX_HITS:
        if end - start > MIN_SLICE_SECONDS:
            mid = (start + end) // 2
            yield from search_window(client, query, start, mid, min_points)
            yield from search_window(client, query, mid, end, min_points)
            return
        log(
            f"  warning: >{MAX_HITS} hits in a {end - start}s slice for {query!r}; truncated"
        )
    pages = page.get("nbPages", 1)
    for p in range(pages):
        if p:
            params["page"] = p
            page = client.get_json("search_by_date", params) or {"hits": []}
        yield from page["hits"]


def iter_hits(
    client: Client, queries: list[str], start: int, end: int, min_points: int
) -> Iterator[dict]:
    """Hits for all queries, each story once."""
    seen: set[str] = set()
    for i, q in enumerate(queries, 1):
        log(f"query {i}/{len(queries)}: {q!r}")
        for hit in search_window(client, q, start, end, min_points):
            oid = str(hit["objectID"])
            if oid not in seen:
                seen.add(oid)
                yield hit


# ------------------------------------------------------------------------ keywords
def keywords_from_dict(path: Path, categories: set[str] | None = None) -> list[str]:
    """Search keywords from tech_dict.yaml.

    Uses `aliases` only. `ambiguous_aliases` ("go", "swift", "bloc") are skipped because they
    bring noise; the tagger resolves them later with context. Also skipped: terms shorter than
    3 characters ("ai") and terms with symbols Algolia ignores ("c++", "c#"), which would turn
    into a bare "c". Multi-word aliases are sent as exact phrases.
    """
    import yaml  # only needed for --from-dict

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for spec in data["technologies"].values():
        if categories and spec.get("category") not in categories:
            continue
        for alias in spec.get("aliases") or []:
            a = str(alias).strip()
            if len(a) < MIN_KEYWORD_LEN or not SAFE_KEYWORD.match(a):
                continue
            out.setdefault(a.lower(), f'"{a}"' if " " in a else a)
    return list(out.values())


# --------------------------------------------------------------------------- files
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def story_path(out: Path, story_id: int, created_at: int) -> Path:
    year = datetime.fromtimestamp(created_at, timezone.utc).year
    return out / str(year) / f"{story_id}.json"


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)  # a crash never leaves a half-written thread


def count_comments(item: dict) -> int:
    n, stack = 0, list(item.get("children") or [])
    while stack:  # iterative: threads can be deeper than the recursion limit
        node = stack.pop()
        n += 1
        stack.extend(node.get("children") or [])
    return n


def parse_when(value: str) -> int:
    """'2021' -> 2021-01-01 UTC; '2026-11-15' -> that day UTC. Returns epoch seconds."""
    try:
        if value.isdigit() and len(value) == 4:
            dt = datetime(int(value), 1, 1, tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r}: use YYYY or YYYY-MM-DD") from None
    return int(dt.timestamp())


# ---------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Download full Hacker News threads from the Algolia API.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "-q",
        "--query",
        action="append",
        default=[],
        metavar="KEYWORD",
        help="keyword to search (repeat for several)",
    )
    ap.add_argument(
        "--from-dict",
        nargs="?",
        const=DEFAULT_DICT,
        type=Path,
        metavar="PATH",
        help="use the aliases in tech_dict.yaml as keywords",
    )
    ap.add_argument(
        "--categories",
        metavar="A,B",
        help="with --from-dict: only these categories (e.g. mobile,state-mgmt)",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="no keyword filter: every story above --min-points",
    )
    ap.add_argument(
        "--since",
        type=parse_when,
        default="2025",
        metavar="YYYY|YYYY-MM-DD",
        help="oldest story to fetch",
    )
    ap.add_argument(
        "--until",
        type=parse_when,
        default=None,
        metavar="YYYY|YYYY-MM-DD",
        help="stories before this date (exclusive); default: now",
    )
    ap.add_argument("--min-points", type=int, default=20, help="minimum story score")
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="stop after N threads downloaded (0 = no limit)",
    )
    ap.add_argument(
        "--refresh-days",
        type=int,
        default=0,
        metavar="DAYS",
        help="also re-download threads already on disk whose story is younger than DAYS",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between requests")
    ap.add_argument(
        "--dry-run", action="store_true", help="list what would be downloaded"
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not (args.query or args.from_dict or args.all):
        ap.error("choose the stories to fetch: -q KEYWORD, --from-dict or --all")
    if args.categories and not args.from_dict:
        ap.error("--categories only works with --from-dict")

    queries = list(args.query)
    if args.from_dict:
        cats = (
            {c.strip() for c in args.categories.split(",")} if args.categories else None
        )
        queries += keywords_from_dict(args.from_dict, cats)
    if args.all:
        queries.append("")  # empty query = every story
    queries = list(dict.fromkeys(queries))
    log(
        f"{len(queries)} search quer{'y' if len(queries) == 1 else 'ies'} "
        f"(at least {len(queries)} requests, about {len(queries) * args.delay / 60:.0f} min of searching)"
    )

    now = utc_now()
    end = args.until if args.until is not None else int(now.timestamp()) + 1
    if args.since >= end:
        log("--since must be earlier than --until")
        return 2

    client = Client(delay=args.delay)
    stats = {"downloaded": 0, "refreshed": 0, "skipped": 0, "failed": 0}
    refresh_cutoff = (
        int((now - timedelta(days=args.refresh_days)).timestamp())
        if args.refresh_days
        else None
    )

    try:
        for hit in iter_hits(client, queries, args.since, end, args.min_points):
            sid, created = int(hit["objectID"]), int(hit["created_at_i"])
            path = story_path(args.out, sid, created)
            exists = path.exists()
            if exists and not (refresh_cutoff and created >= refresh_cutoff):
                stats["skipped"] += 1
                continue
            if args.limit and stats["downloaded"] + stats["refreshed"] >= args.limit:
                log(f"reached --limit {args.limit}")
                break

            label = (
                f"{sid} ({hit.get('points') or 0} pts) {(hit.get('title') or '')[:70]}"
            )
            if args.dry_run:
                log(f"would {'refresh' if exists else 'download'}: {label}")
                continue
            try:
                item = client.get_json(f"items/{sid}")
                if item is None:
                    raise RuntimeError("item not found (404)")
                item["fetched_at"] = utc_now().isoformat(timespec="seconds")
                write_json_atomic(path, item)
            except (
                HTTPError,
                URLError,
                TimeoutError,
                ConnectionError,
                RuntimeError,
                OSError,
            ) as e:
                stats["failed"] += 1
                log(f"FAILED {label}: {e}")
                continue
            stats["refreshed" if exists else "downloaded"] += 1
            log(
                f"{'refreshed' if exists else 'saved'} {label} -> {count_comments(item)} comments"
            )
    except KeyboardInterrupt:
        log("interrupted")
        return 130

    log("done: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
