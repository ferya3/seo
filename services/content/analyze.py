"""Which keywords the site actually has a page for, and which pages fight
each other over the same one.

This is the first analysis that needs two earlier steps at once: the crawl
knows what pages exist and what each one says it is about, the keyword research
knows what people search for. Neither answers the question alone, and no rule
inside the engine can, because the engine never sees the keyword research.

Two findings come out of that pairing:

  * **Gaps** — a researched keyword with no page targeting it. That is a page
    to write, named, with the demand figure attached.
  * **Cannibalisation** — one keyword targeted by several pages. They split
    the signal and rank worse than either would alone, and nothing in a
    per-page audit can see it, because each page on its own looks fine.

Matching is done against what a page *declares*: title, meta description and
headings. Body text is deliberately not used — it is the entire crawl again in
memory, and a page that mentions a term once in a paragraph is not targeting
it. That is a real limit and it is stated in the report, not hidden.

Pure functions of two dictionaries: no network, no database, no clock.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# The Persian spelling rules moved to `shared.text` when the competitor service
# needed the same ones. They are re-exported here because they were part of
# this module's surface first, and because "which yeh" has to have exactly one
# answer across the whole system.
from shared.text import FOLD, NON_WORD, STOPWORDS, normalise, words  # noqa: F401

# How much of a keyword's words must appear in what a page declares before the
# page counts as targeting it. Two-thirds means "کفش ورزشی مردانه" matches a
# page about "کفش ورزشی مردانه ساق بلند" but not one that merely says "کفش".
MATCH_RATIO = 0.67


@dataclass
class Page:
    url: str
    title: str | None = None
    description: str | None = None
    headings: list[str] = field(default_factory=list)
    word_count: int = 0
    indexable: bool = True

    @property
    def declared(self) -> str:
        """Everything the page says about itself, in one string.

        Weighted by repetition rather than by a score: the title is the
        strongest signal, so it is counted twice, and h1 once more than the
        rest. Cruder than a weighted model and far easier to explain when
        someone asks why a page matched.
        """
        parts = [self.title or "", self.title or "", self.description or ""]
        parts.extend(self.headings)
        return " ".join(parts)


def build_pages(report: dict[str, Any]) -> list[Page]:
    pages: list[Page] = []
    for row in report.get("pages") or []:
        if not row.get("url"):
            continue
        headings = [
            str(h.get("text") or "") for h in (row.get("headings") or [])
            if isinstance(h, dict)
        ]
        pages.append(Page(
            url=str(row["url"]),
            title=row.get("title"),
            description=row.get("description"),
            headings=headings,
            word_count=int(row.get("words") or 0),
            indexable=bool(row.get("indexable")),
        ))
    return pages


def build_keywords(research: dict[str, Any]) -> list[dict[str, Any]]:
    """Accepts either the research service's report or the event preview.

    They carry the same rows under different names, and a consumer that only
    handled one of them would silently analyse nothing.
    """
    rows = research.get("keywords") or research.get("top_keywords") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, str):
            out.append({"keyword": row, "demand": 0, "intent": "unknown"})
        elif isinstance(row, dict) and row.get("keyword"):
            out.append({
                "keyword": str(row["keyword"]),
                "demand": int(row.get("demand") or 0),
                "intent": str(row.get("intent") or "unknown"),
            })
    return out


def targets(page: Page, keyword: str) -> bool:
    """Whether the page declares itself to be about this keyword."""
    terms = words(keyword)
    if not terms:
        return False
    declared = set(words(page.declared))
    hits = sum(1 for term in terms if term in declared)
    return hits / len(terms) >= MATCH_RATIO


def match(pages: list[Page], keywords: list[dict[str, Any]]) -> dict[str, list[Page]]:
    """Keyword -> the pages targeting it. The one pass everything else reads."""
    found: dict[str, list[Page]] = defaultdict(list)
    indexable = [p for p in pages if p.indexable]

    for row in keywords:
        keyword = row["keyword"]
        found[keyword] = [p for p in indexable if targets(p, keyword)]
    return found


def gaps(matched: dict[str, list[Page]], keywords: list[dict[str, Any]],
         limit: int = 25) -> list[dict[str, Any]]:
    """Keywords with no page. Ordered by demand — the biggest missing page
    first, not the alphabetically luckiest."""
    by_keyword = {row["keyword"]: row for row in keywords}
    missing = [
        {
            "keyword": keyword,
            "demand": by_keyword.get(keyword, {}).get("demand", 0),
            "intent": by_keyword.get(keyword, {}).get("intent", "unknown"),
        }
        for keyword, pages in matched.items() if not pages
    ]
    return sorted(missing, key=lambda row: (-row["demand"], row["keyword"]))[:limit]


def cannibalisation(matched: dict[str, list[Page]], limit: int = 25) -> list[dict[str, Any]]:
    """Keywords several pages target at once.

    Invisible to a per-page audit: each of those pages looks perfectly fine on
    its own. Only holding the whole site and the keyword list together shows
    that three of them are competing for one query.
    """
    clashes = [
        {
            "keyword": keyword,
            "pages": [
                {"url": p.url, "title": p.title, "words": p.word_count}
                for p in sorted(pages, key=lambda p: -p.word_count)[:5]
            ],
            "count": len(pages),
        }
        for keyword, pages in matched.items() if len(pages) > 1
    ]
    return sorted(clashes, key=lambda row: (-row["count"], row["keyword"]))[:limit]


def untargeted(pages: list[Page], matched: dict[str, list[Page]],
               limit: int = 25) -> list[dict[str, Any]]:
    """Indexable pages that match none of the researched keywords.

    Not automatically a fault — an About page should not target a commercial
    query — which is why this is a list to look at and not a count of errors.
    """
    claimed = {p.url for pages_for in matched.values() for p in pages_for}
    return [
        {"url": p.url, "title": p.title, "words": p.word_count}
        for p in pages
        if p.indexable and p.url not in claimed
    ][:limit]


def summarise(report: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    """The whole analysis, from one crawl report and one keyword study."""
    pages = build_pages(report)
    keywords = build_keywords(research)
    matched = match(pages, keywords)

    covered = [k for k, found in matched.items() if found]
    indexable = [p for p in pages if p.indexable]

    return {
        "pages": len(pages),
        "indexable_pages": len(indexable),
        "keywords": len(keywords),
        "covered": len(covered),
        "coverage": round(len(covered) / len(keywords) * 100, 1) if keywords else 0.0,
        "gaps": gaps(matched, keywords),
        "cannibalisation": cannibalisation(matched),
        "untargeted_pages": untargeted(pages, matched),
        # Said out loud in the report rather than left for someone to discover:
        # a page can cover a topic in its body and still be counted as a gap.
        "method": (
            "تطبیق بر اساس چیزی است که صفحه درباره‌ی خودش اعلام می‌کند: عنوان، "
            "توضیح متا و تیترها. متن بدنه بررسی نمی‌شود، پس ممکن است صفحه‌ای "
            "موضوعی را پوشش داده باشد و اینجا شکاف شمرده شود."
        ),
    }
