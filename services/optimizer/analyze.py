"""What to rewrite on a page, and the constraints the rewrite has to meet.

Every service before this one reports. This is the first that proposes, and
that changes what "working without an API key" has to mean.

The split is deliberate and it is the whole design:

  * The rule layer decides **which pages matter, what is wrong, and what any
    fix must satisfy** — the target keyword, the length window, whether the
    keyword has to appear. It also produces a candidate wherever one can be
    derived without inventing anything: a title too long can be trimmed at a
    word boundary; a missing title can be built from the page's own H1. It
    never fabricates prose it does not have.
  * The model layer (rewrite.py) writes the copy. Without a key you get a
    precise brief instead of a sentence someone else made up, which is the
    honest version of "the free path always works".

Pure functions of a crawl report and a keyword study.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from services.content.analyze import normalise, words

# Google truncates a title link around sixty characters and a snippet around a
# hundred and sixty. Under the lower bound is not an error — it is a signal
# that the page is leaving room it could be using.
TITLE_MIN, TITLE_MAX = 25, 60
DESC_MIN, DESC_MAX = 70, 160

# Separators sites use between a page title and their brand.
BRAND_SEPARATORS = ("|", "-", "–", "—", "»", "·", ":")


@dataclass
class Page:
    url: str
    title: str = ""
    description: str = ""
    h1: str = ""
    headings: list[str] = field(default_factory=list)
    words: int = 0
    indexable: bool = True

    @property
    def slug_words(self) -> list[str]:
        """The URL's own words — the last honest source of what a page is
        about when it has no title and no headings."""
        path = unquote(urlsplit(self.url).path)
        return [w for w in re.split(r"[/\-_.]+", path) if w and not w.isdigit()]


def build_pages(report: dict[str, Any]) -> list[Page]:
    pages: list[Page] = []
    for row in report.get("pages") or []:
        if not row.get("url"):
            continue
        headings = [
            str(h.get("text") or "").strip()
            for h in (row.get("headings") or []) if isinstance(h, dict)
        ]
        h1 = next(
            (str(h.get("text") or "").strip()
             for h in (row.get("headings") or [])
             if isinstance(h, dict) and h.get("level") == 1),
            "",
        )
        pages.append(Page(
            url=str(row["url"]),
            title=(row.get("title") or "").strip(),
            description=(row.get("description") or "").strip(),
            h1=h1,
            headings=[h for h in headings if h],
            words=int(row.get("words") or 0),
            indexable=bool(row.get("indexable")),
        ))
    return pages


# ------------------------------------------------------------------ matching


def keyword_for(page: Page, keywords: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The keyword this page is closest to already targeting.

    Deliberately not "the highest-demand keyword": telling someone to rewrite
    their contact page around a commercial query is how a tool gets ignored.
    The best match among what the page already says, and nothing if there is
    no overlap at all.
    """
    declared = set(words(" ".join([page.title, page.h1, page.description, *page.headings])))
    if not declared:
        return None

    scored: list[tuple[float, int, int, dict[str, Any]]] = []
    for row in keywords:
        terms = words(row.get("keyword", ""))
        if not terms:
            continue
        overlap = sum(1 for term in terms if term in declared) / len(terms)
        if overlap >= 0.5:
            # Three keys in order: how much of the keyword the page covers,
            # then how specific the keyword is, then demand. Specificity comes
            # before demand because a page that fully covers "کفش دویدن مردانه"
            # is about that, not about the far more searched "کفش" it also
            # contains — and rewriting it around the broad term would make it
            # compete with every other page on the site.
            scored.append((overlap, len(terms), row.get("demand", 0), row))

    if not scored:
        return None
    return max(scored, key=lambda item: item[:3])[3]


# ------------------------------------------------------------------ the fixes


def trim_title(title: str, limit: int = TITLE_MAX) -> str:
    """Cut at a word boundary, dropping the brand suffix first.

    A hard character cut leaves "فروشگاه کفش ورزشی و کف" on the results page.
    Dropping the brand is what a person would do first, because it is the part
    the reader can already see in the domain.
    """
    title = " ".join(title.split())
    if len(title) <= limit:
        return title

    for separator in BRAND_SEPARATORS:
        head = title.rsplit(f" {separator} ", 1)[0].strip()
        if head and head != title and len(head) <= limit:
            return head

    cut = title[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ،-|–—:")


def title_fix(page: Page, keyword: str | None) -> dict[str, Any] | None:
    """What is wrong with the title, and a candidate when one can be derived."""
    title = page.title
    has_keyword = bool(keyword) and _covers(title, keyword)

    if not title:
        # Built from the page's own H1 rather than from nothing. If there is no
        # H1 either, the brief says so and the model writes it.
        candidate = page.h1 or " ".join(page.slug_words[-3:]) or ""
        return _fix("title", "missing", title, trim_title(candidate) or None, keyword,
                    "صفحه اصلاً عنوان ندارد.")

    if len(title) > TITLE_MAX:
        return _fix("title", "too_long", title, trim_title(title), keyword,
                    f"عنوان {len(title)} کاراکتر است و در نتایج بریده می‌شود.")

    if keyword and not has_keyword:
        # No candidate: putting the keyword in mechanically produces the kind
        # of stuffed title this tool is supposed to argue against.
        return _fix("title", "missing_keyword", title, None, keyword,
                    f"عبارت هدف «{keyword}» در عنوان نیست.")

    if len(title) < TITLE_MIN:
        return _fix("title", "too_short", title, None, keyword,
                    f"عنوان فقط {len(title)} کاراکتر است و جای استفاده‌نشده دارد.")
    return None


def description_fix(page: Page, keyword: str | None) -> dict[str, Any] | None:
    description = page.description

    if not description:
        return _fix("description", "missing", description, None, keyword,
                    "توضیح متا ندارد، پس گوگل خودش متنی از صفحه برمی‌دارد.")
    if len(description) > DESC_MAX:
        return _fix("description", "too_long", description, None, keyword,
                    f"توضیح {len(description)} کاراکتر است و بریده می‌شود.")
    if len(description) < DESC_MIN:
        return _fix("description", "too_short", description, None, keyword,
                    f"توضیح فقط {len(description)} کاراکتر است.")
    if keyword and not _covers(description, keyword):
        return _fix("description", "missing_keyword", description, None, keyword,
                    f"عبارت هدف «{keyword}» در توضیح نیست.")
    return None


def h1_fix(page: Page, keyword: str | None) -> dict[str, Any] | None:
    if not page.h1:
        candidate = page.title or None
        return _fix("h1", "missing", "", candidate, keyword, "صفحه تیتر H1 ندارد.")
    if keyword and not _covers(page.h1, keyword):
        return _fix("h1", "missing_keyword", page.h1, None, keyword,
                    f"عبارت هدف «{keyword}» در H1 نیست.")
    return None


def _fix(field_name, problem, current, candidate, keyword, why) -> dict[str, Any]:
    limits = {
        "title": (TITLE_MIN, TITLE_MAX),
        "description": (DESC_MIN, DESC_MAX),
        "h1": (0, 0),
    }[field_name]
    return {
        "field": field_name,
        "problem": problem,
        "current": current,
        # Present only when it could be derived from what the page already has.
        # Null means "this needs writing", which is a brief, not a failure.
        "candidate": candidate,
        "keyword": keyword,
        "min_length": limits[0] or None,
        "max_length": limits[1] or None,
        "why_fa": why,
    }


def _covers(text: str, keyword: str) -> bool:
    """The same two-thirds rule the content service uses, so a page is not
    "targeting" a keyword in one report and "missing" it in another."""
    terms = words(keyword)
    if not terms:
        return False
    present = set(normalise(text).split())
    return sum(1 for term in terms if term in present) / len(terms) >= 0.67


# ------------------------------------------------------------------- the plan


def page_plan(page: Page, keywords: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Everything worth changing on one page, or nothing."""
    row = keyword_for(page, keywords)
    keyword = row["keyword"] if row else None

    fixes = [f for f in (title_fix(page, keyword), description_fix(page, keyword),
                         h1_fix(page, keyword)) if f]
    if not fixes:
        return None

    return {
        "url": page.url,
        "keyword": keyword,
        "demand": row.get("demand", 0) if row else 0,
        "words": page.words,
        "fixes": fixes,
        "source": "rules",
    }


def rank(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Most demand first, then the page with the most wrong with it.

    A page nobody searches for is a real page, but it is not the one to spend
    the first hour on.
    """
    return sorted(plans, key=lambda p: (-p["demand"], -len(p["fixes"]), p["url"]))


def summarise(
    report: dict[str, Any], research: dict[str, Any], limit: int = 5
) -> dict[str, Any]:
    """The pages worth rewriting, with what to change on each."""
    from services.content.analyze import build_keywords

    pages = [p for p in build_pages(report) if p.indexable]
    keywords = build_keywords(research)

    plans = rank([plan for plan in (page_plan(p, keywords) for p in pages) if plan])
    counts: dict[str, int] = {}
    for plan in plans:
        for fix in plan["fixes"]:
            counts[fix["problem"]] = counts.get(fix["problem"], 0) + 1

    return {
        "pages_examined": len(pages),
        "pages_with_fixes": len(plans),
        "fixes": sum(len(p["fixes"]) for p in plans),
        "by_problem": dict(sorted(counts.items())),
        # Bounded on purpose: this list is meant to be worked through, and a
        # hundred rewrites is a list nobody starts.
        "plans": plans[:limit],
        "limit": limit,
    }
