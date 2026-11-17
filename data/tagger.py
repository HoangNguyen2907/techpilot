"""Tag text with technologies from tech_dict.yaml.

Rules:
  * `aliases` always match (case-insensitive, whole token).
  * `ambiguous_aliases` ("go", "swift", "bloc", ...) match only when a `context` term
    appears within CONTEXT_WINDOW characters of the match, or in the optional
    `context_text` (title + url for a story, story title for a comment).
  * An ambiguous match that overlaps a longer unambiguous alias is dropped, so
    "React Native" tags react-native but not react, and "Claude Code" tags
    claude-code but not claude.
  * Text is cleaned first: HTML tags removed, entities unescaped, URLs stripped.
  * Title first: tag_story() reports whether each technology was found in the title.

Optional top-level keys in tech_dict.yaml:
  implies:        {tech: [other, ...]}  one-level; adds `other` with via="implied"
  versioned:      [tech, ...]           "python3", "vue3", "react18" match the base alias
  case_sensitive: {tech: [term, ...]}   listed terms match only as UPPER or Title case
                                        ("Go" yes, "go" no; "ML" yes, "ml" no)

The dictionary is validated on load; problems raise ValueError (strict=True, default).

Requires: pyyaml  (uv add pyyaml)

CLI:
    uv run data/tagger.py "Why we moved our Flutter app from Provider to Riverpod"
    uv run data/tagger.py --bench
"""

from __future__ import annotations

import hashlib
import html
import re
import sys
import time
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

DEFAULT_DICT = Path(__file__).resolve().with_name("tech_dict.yaml")
CONTEXT_WINDOW = 200  # characters on each side of an ambiguous match

_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_URL_SPLIT_RE = re.compile(r"[/._\-?=&+]+")


_CODE_RE = re.compile(r"<pre[^>]*>.*?</pre>|<code[^>]*>.*?</code>", re.I | re.S)
_ZW_RE = re.compile(r"[\u200b-\u200d\u2060\ufeff\u00ad]")
_PATH_URL_RE = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|io|dev)/\S*", re.I)


def clean_text(text, strip_urls=True, is_html=True):
    if not text:
        return ""
    if is_html:
        text = _CODE_RE.sub(" ", text)
        text = re.sub(r"<p>", "\n", text, flags=re.I)
        text = _HTML_TAG_RE.sub(" ", text)
        text = html.unescape(text)
        text = re.sub(r"(?m)^\s*>.*$", " ", text)
    text = unicodedata.normalize("NFKC", text)
    text = _ZW_RE.sub("", text)
    if strip_urls:
        text = _URL_RE.sub(" ", text)
        text = _PATH_URL_RE.sub(" ", text)
    return text


def _url_context(url: str | None) -> str:
    """Words from a story URL, used only as context for ambiguous aliases."""
    if not url:
        return ""
    p = urlparse(url if "//" in url else "//" + url)
    raw = f"{p.netloc}{p.path}"
    return f"{raw} {_URL_SPLIT_RE.sub(' ', raw)}"


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _term_pattern(
    term: str, case_sensitive: bool = False, versioned: bool = False
) -> str:
    """Regex for one alias/context term with symbol-safe boundaries.

    \\b does not work for terms like "c++", "c#" or ".net", so the left side uses (?<!\\w)
    and the right side (?!\\w) only when the term ends in a word character. Spaces inside a
    term match any run of whitespace. `versioned` lets "python3" / "vue3" / "react18" match
    the base term (lookahead only, so the match text stays the base term).
    """
    term = term.strip()

    def body(t: str) -> str:
        return r"\s+".join(re.escape(w) for w in t.split())

    if case_sensitive:
        variants = list(dict.fromkeys([term.upper(), term.title()]))
        core = "(?-i:" + "|".join(body(v) for v in variants) + ")"
    else:
        core = body(term)

    if re.match(r"\w", term[-1]):
        if versioned and term[-1].isalpha():
            suffix = r"(?=\d{1,2}(?:\.\d+)*(?!\w)|(?!\w))"
        else:
            suffix = r"(?!\w)"
    else:
        suffix = ""
    return f"(?<!\\w){core}{suffix}"


def _plain_alternation(terms: list[tuple[str, bool, bool]]) -> re.Pattern | None:
    """Plain alternation, longest first. Handles every flag combination."""
    if not terms:
        return None
    uniq = {_norm(t): (t, cs, ver) for t, cs, ver in terms}
    ordered = sorted(uniq.values(), key=lambda x: len(_norm(x[0])), reverse=True)
    return re.compile("|".join(_term_pattern(*t) for t in ordered), re.IGNORECASE)


def _trie_regex(words: list[str]) -> str:
    """Prefix-tree regex: the engine dispatches on the first characters instead of trying
    thousands of alternatives one by one. Optional continuations are greedy, so the
    longest term that passes the boundary check wins."""
    trie: dict = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = {}

    def emit(node: dict) -> str:
        alts = [
            (r"\s+" if ch == " " else re.escape(ch)) + emit(node[ch])
            for ch in sorted(k for k in node if k)
        ]
        if not alts:
            return ""
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        return f"(?:{body})?" if "" in node else body

    return emit(trie)


class _Matcher:
    """Finds dictionary terms in text. Plain terms go through one trie regex; terms with
    special rules (case-sensitive, versioned, or ending in a symbol such as "c++") go through
    a small plain alternation. Hits from both are merged leftmost-longest, non-overlapping."""

    def __init__(self, terms: list[tuple[str, bool, bool]]):
        uniq = {_norm(t): (t, cs, ver) for t, cs, ver in terms}
        simple = [
            n
            for n, (t, cs, ver) in uniq.items()
            if not cs and not ver and re.match(r"\w", n[-1])
        ]
        special = [v for n, v in uniq.items() if n not in set(simple)]
        self._res: list[re.Pattern] = []
        if simple:
            self._res.append(
                re.compile(rf"(?<!\w)(?:{_trie_regex(simple)})(?!\w)", re.IGNORECASE)
            )
        extra = _plain_alternation(special)
        if extra is not None:
            self._res.append(extra)

    def finditer(self, text: str) -> list[tuple[int, int, str]]:
        hits: list[tuple[int, int]] = []
        for r in self._res:
            hits.extend(m.span() for m in r.finditer(text))
        if len(self._res) > 1:
            hits.sort(key=lambda x: (x[0], -x[1]))
            merged, last = [], -1
            for s, e in hits:
                if s >= last:
                    merged.append((s, e))
                    last = e
            hits = merged
        return [(s, e, text[s:e]) for s, e in hits]


def _compile_alternation(terms: list[tuple[str, bool, bool]]) -> _Matcher | None:
    return _Matcher(terms) if terms else None


def validate_dict(data: dict) -> list[str]:
    """Return a list of problems (empty list = dictionary is consistent)."""
    errs: list[str] = []
    techs = data.get("technologies")
    if not isinstance(techs, dict) or not techs:
        return ["`technologies` is missing or empty"]
    cats = set(data.get("categories") or [])
    seen: dict[str, str] = {}  # normalized alias/ambiguous alias -> owner

    for tech, spec in techs.items():
        if not isinstance(spec, dict):
            errs.append(f"{tech}: entry must be a mapping")
            continue
        if spec.get("category") not in cats:
            errs.append(f"{tech}: unknown category {spec.get('category')!r}")

        def strs(name: str) -> list[str]:
            vals = spec.get(name) or []
            if not isinstance(vals, list):
                errs.append(f"{tech}.{name}: must be a list")
                return []
            out = []
            for v in vals:
                if not isinstance(v, str) or not v.strip():
                    errs.append(
                        f"{tech}.{name}: bad term {v!r} (quote values such as 'no', 'on', 'yes')"
                    )
                else:
                    out.append(v)
            return out

        aliases, amb, ctx = strs("aliases"), strs("ambiguous_aliases"), strs("context")
        for a in aliases + amb:
            n = _norm(a)
            if n in seen:
                errs.append(f"duplicate alias {n!r}: {seen[n]} and {tech}")
            else:
                seen[n] = tech
        if amb and not ctx:
            errs.append(f"{tech}: ambiguous_aliases without `context` can never match")
        ctx_norm = {_norm(c) for c in ctx}
        for a in amb:
            if _norm(a) in ctx_norm:
                errs.append(f"{tech}: ambiguous alias {a!r} is in its own context")

    for tech, targets in (data.get("implies") or {}).items():
        if tech not in techs:
            errs.append(f"implies: unknown tech {tech!r}")
        for t in targets or []:
            if t not in techs:
                errs.append(f"implies: {tech} -> unknown tech {t!r}")
            if t == tech:
                errs.append(f"implies: {tech} implies itself")

    for tech in data.get("versioned") or []:
        if tech not in techs:
            errs.append(f"versioned: unknown tech {tech!r}")

    for tech, terms in (data.get("case_sensitive") or {}).items():
        spec = techs.get(tech)
        if spec is None:
            errs.append(f"case_sensitive: unknown tech {tech!r}")
            continue
        own = {
            _norm(a)
            for a in (spec.get("aliases") or []) + (spec.get("ambiguous_aliases") or [])
        }
        for t in terms or []:
            if _norm(t) not in own:
                errs.append(f"case_sensitive: {t!r} is not an alias of {tech}")
    return errs


@dataclass
class TagMatch:
    tech: str
    count: int = 0
    via: str = "alias"  # "alias", "context" or "implied"
    terms: set[str] = field(default_factory=set)


class Tagger:
    def __init__(self, dict_path: Path | str = DEFAULT_DICT, strict: bool = True):
        raw = Path(dict_path).read_bytes()
        data = yaml.safe_load(raw.decode("utf-8"))
        if strict:
            errs = validate_dict(data)
            if errs:
                raise ValueError(
                    f"{dict_path}: {len(errs)} problem(s)\n  - " + "\n  - ".join(errs)
                )

        # Store this next to story_tech rows so you know which stories need re-tagging.
        self.dict_version = (
            f"v{data.get('version', '?')}-{hashlib.sha1(raw).hexdigest()[:8]}"
        )

        self.techs: dict = data["technologies"]
        self.category = {k: v["category"] for k, v in self.techs.items()}
        self.implies: dict[str, list[str]] = {
            k: list(v or []) for k, v in (data.get("implies") or {}).items()
        }
        versioned = set(data.get("versioned") or [])
        cs_terms = {
            t: {_norm(x) for x in (terms or [])}
            for t, terms in (data.get("case_sensitive") or {}).items()
        }

        alias_map: dict[str, str] = {}
        amb_map: dict[str, str] = {}
        alias_terms: list[tuple[str, bool, bool]] = []
        amb_terms: list[tuple[str, bool, bool]] = []
        self._context: dict[str, re.Pattern | None] = {}
        for tech, spec in self.techs.items():
            ver = tech in versioned
            cs = cs_terms.get(tech, set())
            for a in spec.get("aliases") or []:
                alias_map[_norm(a)] = tech
                alias_terms.append((a, _norm(a) in cs, ver))
            for a in spec.get("ambiguous_aliases") or []:
                amb_map[_norm(a)] = tech
                amb_terms.append((a, _norm(a) in cs, ver))
            if spec.get("ambiguous_aliases"):
                self._context[tech] = _plain_alternation(
                    [(c, False, False) for c in spec.get("context") or []]
                )
        self._alias_map, self._amb_map = alias_map, amb_map
        self._alias_re = _compile_alternation(alias_terms)
        self._amb_re = _compile_alternation(amb_terms)

    # ------------------------------------------------------------------ core
    def tag(
        self, text: str, context_text: str | None = None, clean: bool = True
    ) -> dict[str, TagMatch]:
        """Tag one piece of text.

        An ambiguous alias needs a context term within CONTEXT_WINDOW characters of the
        match in `text`, or anywhere in `context_text` (use it for a title or story URL).
        """
        text = clean_text(text) if clean else (text or "")
        if not text:
            return {}
        extra = (
            (clean_text(context_text) if clean else context_text)
            if context_text
            else ""
        )
        found: dict[str, TagMatch] = {}
        alias_spans: list[tuple[int, int]] = []

        if self._alias_re:
            for s, e, g in self._alias_re.finditer(text):  # sorted, non-overlapping
                alias_spans.append((s, e))
                tech = self._alias_map[_norm(g)]
                tm = found.setdefault(tech, TagMatch(tech, via="alias"))
                tm.count += 1
                tm.terms.add(_norm(g))

        if self._amb_re:
            starts = [s for s, _ in alias_spans]

            def overlaps_alias(s: int, e: int) -> bool:
                i = bisect_right(starts, s)
                if i and alias_spans[i - 1][1] > s:
                    return True
                return i < len(alias_spans) and alias_spans[i][0] < e

            n = len(text)
            for s, e, g in self._amb_re.finditer(text):
                if overlaps_alias(s, e):
                    continue  # part of a longer, unambiguous alias
                tech = self._amb_map[_norm(g)]
                ctx = self._context.get(tech)
                if ctx is None:
                    continue
                near = ctx.search(
                    text, max(0, s - CONTEXT_WINDOW), min(n, e + CONTEXT_WINDOW)
                )
                if not near and not (extra and ctx.search(extra)):
                    continue
                tm = found.setdefault(tech, TagMatch(tech, via="context"))
                tm.count += 1
                tm.terms.add(_norm(g))

        for tech in list(found):  # one level only, no chains
            for imp in self.implies.get(tech, ()):
                if imp not in found:
                    found[imp] = TagMatch(imp, count=found[tech].count, via="implied")
        return found

    def tags(self, text: str) -> list[str]:
        return sorted(self.tag(text))

    # ------------------------------------------------------------- stories
    def tag_story(self, title: str, body: str = "", url: str = "") -> dict[str, dict]:
        """Tag a story. Returns {tech: {"in_title": bool, "count": int}}.

        Title ambiguity can be confirmed by the title, the URL words or the start of the
        body; body ambiguity by the text around the match, the title or the URL words.
        """
        title_c, body_c = clean_text(title), clean_text(body)
        url_words = _url_context(url)
        out: dict[str, dict] = {}
        for tech, tm in self.tag(
            title_c, context_text=f"{url_words}\n{body_c[:300]}", clean=False
        ).items():
            out[tech] = {"in_title": True, "count": tm.count}
        for tech, tm in self.tag(
            body_c, context_text=f"{title_c}\n{url_words}", clean=False
        ).items():
            if tech in out:
                out[tech]["count"] += tm.count
            else:
                out[tech] = {"in_title": False, "count": tm.count}
        return out

    def tag_comment(self, text: str, story_title: str = "") -> dict[str, TagMatch]:
        """Tag a comment; the story title helps confirm ambiguous aliases."""
        return self.tag(text, context_text=story_title)


def _bench(t: Tagger) -> None:
    sample = (
        "<p>We moved our Flutter app from Provider to Riverpod. Go is great for the backend, "
        'but I use Postgres and Redis. See <a href="https://github.com/x/y">https://github.com/x/y</a> '
        "for the Kubernetes setup, and a cloud provider bill.</p>" * 3
    )
    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        t.tag(sample)
    dt = time.perf_counter() - t0
    print(f"{n} texts x {len(sample)} chars in {dt:.2f}s ({dt / n * 1000:.2f} ms/text)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    t = Tagger()
    if sys.argv[1] == "--bench":
        _bench(t)
    else:
        for tech, tm in sorted(t.tag(" ".join(sys.argv[1:])).items()):
            print(
                f"{tech:<20} {t.category[tech]:<14} count={tm.count} via={tm.via} terms={sorted(tm.terms)}"
            )
