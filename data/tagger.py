"""Tag text with technologies from tech_dict.yaml.

Rules:
  * `aliases` always match (case-insensitive, whole token).
  * `ambiguous_aliases` ("go", "swift", "bloc", ...) match only when at least one
    `context` term appears in the context text.
  * An ambiguous match that overlaps a longer unambiguous alias is dropped, so
    "React Native" tags react-native but not react, and "Claude Code" tags
    claude-code but not claude.
  * Title first: tag_story() reports whether each technology was found in the title.

Requires: pyyaml  (uv add pyyaml)

CLI demo:
    uv run data/tagger.py "Why we moved our Flutter app from Provider to Riverpod"
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_DICT = Path(__file__).resolve().with_name("tech_dict.yaml")


def _term_pattern(term: str) -> str:
    """Regex for one alias/context term with symbol-safe boundaries.

    \\b does not work for terms like "c++", "c#" or ".net core", so the left side uses
    (?<!\\w) and the right side uses (?!\\w) only when the term ends in a word character.
    Spaces inside a term match any run of whitespace.
    """
    words = term.strip().split()
    body = r"\s+".join(re.escape(w) for w in words)
    suffix = r"(?!\w)" if re.match(r"\w", term.strip()[-1]) else ""
    return f"(?<!\\w){body}{suffix}"


def _compile_alternation(terms: list[str]) -> re.Pattern | None:
    if not terms:
        return None
    ordered = sorted(set(terms), key=len, reverse=True)  # longest first
    return re.compile("|".join(_term_pattern(t) for t in ordered), re.IGNORECASE)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


@dataclass
class TagMatch:
    tech: str
    count: int = 0
    via: str = "alias"  # "alias" or "context"
    terms: set[str] = field(default_factory=set)


class Tagger:
    def __init__(self, dict_path: Path | str = DEFAULT_DICT):
        data = yaml.safe_load(Path(dict_path).read_text(encoding="utf-8"))
        self.techs: dict = data["technologies"]
        self.category = {k: v["category"] for k, v in self.techs.items()}

        alias_map: dict[str, str] = {}
        amb_map: dict[str, str] = {}
        self._context: dict[str, re.Pattern] = {}
        for tech, spec in self.techs.items():
            for a in spec.get("aliases") or []:
                alias_map[_norm(a)] = tech
            for a in spec.get("ambiguous_aliases") or []:
                amb_map[_norm(a)] = tech
            if spec.get("ambiguous_aliases"):
                self._context[tech] = _compile_alternation(spec.get("context") or [])
        self._alias_map, self._amb_map = alias_map, amb_map
        self._alias_re = _compile_alternation(list(alias_map))
        self._amb_re = _compile_alternation(list(amb_map))

    # ------------------------------------------------------------------ core
    def tag(self, text: str, context_text: str | None = None) -> dict[str, TagMatch]:
        """Tag one piece of text. Ambiguous aliases look for context in `context_text`
        (defaults to `text`)."""
        if not text:
            return {}
        context_text = text if context_text is None else context_text
        found: dict[str, TagMatch] = {}
        alias_spans: list[tuple[int, int]] = []

        if self._alias_re:
            for m in self._alias_re.finditer(text):
                alias_spans.append(m.span())
                tech = self._alias_map[_norm(m.group())]
                tm = found.setdefault(tech, TagMatch(tech, via="alias"))
                tm.count += 1
                tm.terms.add(_norm(m.group()))

        if self._amb_re:
            for m in self._amb_re.finditer(text):
                s, e = m.span()
                if any(s < ae and as_ < e for as_, ae in alias_spans):
                    continue  # part of a longer, unambiguous alias
                tech = self._amb_map[_norm(m.group())]
                ctx = self._context.get(tech)
                if ctx is None or not ctx.search(context_text):
                    continue
                tm = found.setdefault(tech, TagMatch(tech, via="context"))
                tm.count += 1
                tm.terms.add(_norm(m.group()))
        return found

    def tags(self, text: str) -> list[str]:
        return sorted(self.tag(text))

    # ------------------------------------------------------------- stories
    def tag_story(self, title: str, body: str = "") -> dict[str, dict]:
        """Tag a story. Context is searched in title + body together.
        Returns {tech: {"in_title": bool, "count": int}}."""
        both = f"{title or ''}\n{body or ''}"
        out: dict[str, dict] = {}
        for tech, tm in self.tag(title or "", context_text=both).items():
            out[tech] = {"in_title": True, "count": tm.count}
        for tech, tm in self.tag(body or "", context_text=both).items():
            if tech in out:
                out[tech]["count"] += tm.count
            else:
                out[tech] = {"in_title": False, "count": tm.count}
        return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    t = Tagger()
    for tech, tm in sorted(t.tag(" ".join(sys.argv[1:])).items()):
        print(
            f"{tech:<20} {t.category[tech]:<14} count={tm.count} via={tm.via} terms={sorted(tm.terms)}"
        )
