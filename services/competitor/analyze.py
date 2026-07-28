"""How your site compares with the sites that outrank you.

Every other analysis here answers a question about your site alone. This one
answers the question people actually ask first — *why are they ahead of me* —
and it is a different shape: several crawls, one of them yours, compared with
each other.

Three findings come out of it:

  * **Where you are behind on discipline.** They describe 95% of their pages
    and you describe 40%. That is not an opinion, it is arithmetic, and it is
    the cheapest work on the list.
  * **Where you are ahead.** A report that only lists deficits is one nobody
    can act on; knowing what to protect matters as much as knowing what to fix.
  * **Themes you do not cover.** Terms several competitors put in their titles
    and headings that appear nowhere in yours. Unlike the content service's
    gaps, these come from what the market writes about rather than from a
    keyword tool — which is the only source available when the keyword tools
    are unreachable.

Two limits are built into the arithmetic rather than written under it:

**A sample is not a site.** Crawls are capped, and comparing "20 pages" with
"20 pages" would say the two sites are the same size when it only says the cap
was the same. So nothing here compares totals. Every metric is a rate or a
median, both of which survive sampling, and the sample size is carried into
the report so a reader can judge it.

**One competitor is an anecdote.** A term is only a theme when at least two
competitors use it; otherwise a single site's house style would read as a gap
in yours.

Pure functions of dictionaries: no network, no database, no clock.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from shared.seo import THIN_WORDS, TITLE_MAX, TITLE_MIN
from shared.text import words

# Below this many pages a median means nothing and a percentage is noise: three
# pages with two titles is "67%". Such a site is still listed and named, but it
# is kept out of the verdicts rather than quietly averaged in.
MIN_SAMPLE = 5

# How far apart two rates have to be before the difference is called one. Under
# this, "62% vs 58%" is two samples of the same practice, not a finding.
RATE_MARGIN = 10.0

# Same idea for medians, as a ratio: a site whose pages run a quarter longer
# than yours is writing the same kind of page. Half again as long is a
# different kind of page, and that is worth telling someone.
LENGTH_MARGIN = 1.5

# A theme has to be used by this many competitors before it counts as one.
MIN_COMPETITORS = 2

# Terms this short are almost always fragments rather than subjects.
MIN_TERM_LENGTH = 3


class NoComparison(ValueError):
    """There is nothing to compare against."""


@dataclass
class Page:
    url: str
    title: str | None = None
    description: str | None = None
    headings: list[dict[str, Any]] = field(default_factory=list)
    word_count: int = 0
    indexable: bool = True

    @property
    def h1(self) -> str | None:
        for heading in self.headings:
            if str(heading.get("level")) in ("1", "h1", "H1"):
                text = str(heading.get("text") or "").strip()
                return text or None
        return None

    @property
    def declared(self) -> str:
        """What the page says it is about: title, description, headings."""
        parts = [self.title or "", self.description or ""]
        parts.extend(str(h.get("text") or "") for h in self.headings)
        return " ".join(parts)


@dataclass
class Site:
    label: str
    pages: list[Page] = field(default_factory=list)
    error: str | None = None

    @property
    def indexable(self) -> list[Page]:
        """Only indexable pages are compared.

        A noindex page is not competing for anything, and counting a site's
        staging pages against its title discipline measures the wrong thing.
        """
        return [p for p in self.pages if p.indexable]


def site_label(report: dict[str, Any]) -> str:
    """The host, which is how a person names a competitor."""
    url = report.get("start_url") or ""
    host = urlsplit(str(url)).netloc.lower()
    return host[4:] if host.startswith("www.") else (host or str(url) or "unknown")


def build_site(report: dict[str, Any], label: str | None = None) -> Site:
    pages = [
        Page(
            url=str(row.get("url")),
            title=row.get("title"),
            description=row.get("description"),
            headings=[h for h in (row.get("headings") or []) if isinstance(h, dict)],
            word_count=int(row.get("words") or 0),
            indexable=bool(row.get("indexable", True)),
        )
        for row in (report.get("pages") or []) if row.get("url")
    ]
    return Site(label=label or site_label(report), pages=pages)


# ----------------------------------------------------------------- the profile


def profile(site: Site) -> dict[str, Any]:
    """One site's habits, as rates and medians only.

    Nothing here is a count of pages, deliberately: see the module docstring.
    `sampled` is reported so the reader can weigh the rest, not so it can be
    compared with anyone else's.
    """
    pages = site.indexable
    lengths = [p.word_count for p in pages]

    return {
        "label": site.label,
        "sampled": len(pages),
        "comparable": len(pages) >= MIN_SAMPLE,
        "titled": _rate(pages, lambda p: bool((p.title or "").strip())),
        "title_in_range": _rate(pages, lambda p: TITLE_MIN <= len(p.title or "") <= TITLE_MAX),
        "described": _rate(pages, lambda p: bool((p.description or "").strip())),
        "has_h1": _rate(pages, lambda p: bool(p.h1)),
        "median_words": int(statistics.median(lengths)) if lengths else 0,
        "thin": _rate(pages, lambda p: p.word_count < THIN_WORDS),
        "median_headings": int(statistics.median([len(p.headings) for p in pages])) if pages else 0,
    }


def _rate(pages: list[Page], predicate) -> float:
    if not pages:
        return 0.0
    return round(100 * sum(1 for p in pages if predicate(p)) / len(pages), 1)


# --------------------------------------------------------------- the verdicts

# The metrics that are compared, and what a higher number means. `thin` is the
# one where less is better, which is exactly the sort of thing that is wrong in
# a report for a year before anyone notices, so it is stated rather than
# assumed.
METRICS = (
    ("titled", "higher", "صفحه‌های دارای عنوان"),
    ("title_in_range", "higher", "عنوان با طول مناسب"),
    ("described", "higher", "صفحه‌های دارای توضیح متا"),
    ("has_h1", "higher", "صفحه‌های دارای H1"),
    ("thin", "lower", "صفحه‌های کم‌محتوا"),
)


def rate_verdicts(mine: dict[str, Any], others: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where you sit against the competitors' median, metric by metric.

    The median rather than the best: one competitor doing something unusually
    well is not the standard, and chasing the maximum on every metric is how a
    comparison report turns into an unfinishable list.
    """
    comparable = [p for p in others if p["comparable"]]
    if not comparable:
        return []

    verdicts = []
    for key, direction, label_fa in METRICS:
        theirs = round(statistics.median([p[key] for p in comparable]), 1)
        ours = mine[key]
        gap = round(ours - theirs, 1)
        better = gap > 0 if direction == "higher" else gap < 0

        verdicts.append({
            "metric": key,
            "label_fa": label_fa,
            "yours": ours,
            "competitor_median": theirs,
            "difference": gap,
            "verdict": "level" if abs(gap) < RATE_MARGIN else ("ahead" if better else "behind"),
        })
    return verdicts


def length_verdict(mine: dict[str, Any], others: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Whether they simply write more than you.

    A ratio rather than a difference: 400 words against 500 is the same
    practice, 400 against 1,600 is a different one, and subtracting cannot tell
    those apart.
    """
    comparable = [p for p in others if p["comparable"] and p["median_words"] > 0]
    if not comparable or not mine["median_words"]:
        return None

    theirs = int(statistics.median([p["median_words"] for p in comparable]))
    ratio = theirs / mine["median_words"]

    if ratio >= LENGTH_MARGIN:
        verdict = "behind"
    elif ratio <= 1 / LENGTH_MARGIN:
        verdict = "ahead"
    else:
        verdict = "level"

    return {
        "yours": mine["median_words"],
        "competitor_median": theirs,
        "ratio": round(ratio, 2),
        "verdict": verdict,
    }


# ----------------------------------------------------------------- the themes


def terms(site: Site) -> Counter:
    """What the site writes about, counted once per page.

    Once per page, not once per mention: a term in the template's header would
    otherwise outrank every subject the site actually covers.
    """
    counter: Counter = Counter()
    for page in site.indexable:
        seen = {w for w in words(page.declared) if len(w) >= MIN_TERM_LENGTH}
        counter.update(seen)
    return counter


def themes(mine: Site, others: list[Site], limit: int = 25) -> list[dict[str, Any]]:
    """Subjects the market writes about and you do not.

    "Do not" is strict: a term used on even one of your pages is not a gap,
    however lightly you cover it. Depth is a different question and this
    comparison cannot see it.
    """
    ours = set(terms(mine))
    usable = [s for s in others if len(s.indexable) >= MIN_SAMPLE]

    coverage: Counter = Counter()
    pages_with: Counter = Counter()
    for site in usable:
        counts = terms(site)
        for term in counts:
            coverage[term] += 1
            pages_with[term] += counts[term]

    rows = [
        {"term": term, "competitors": used, "their_pages": pages_with[term]}
        for term, used in coverage.items()
        if used >= MIN_COMPETITORS and term not in ours
    ]
    # Most competitors first, then how much of their sites it touches: a term
    # every competitor covers on many pages is a subject, not a word.
    rows.sort(key=lambda row: (-row["competitors"], -row["their_pages"], row["term"]))
    return rows[:limit]


def shared_themes(mine: Site, others: list[Site], limit: int = 15) -> list[dict[str, Any]]:
    """Subjects you and the market both cover — the ground you are on.

    Useful for the opposite reason to `themes`: these are the terms where you
    are already in the conversation, so a ranking check on them is worth more
    than one on a term you have never written about.
    """
    ours = terms(mine)
    usable = [s for s in others if len(s.indexable) >= MIN_SAMPLE]

    coverage: Counter = Counter()
    for site in usable:
        for term in terms(site):
            coverage[term] += 1

    rows = [
        {"term": term, "competitors": used, "your_pages": ours[term]}
        for term, used in coverage.items()
        if used >= MIN_COMPETITORS and term in ours
    ]
    rows.sort(key=lambda row: (-row["competitors"], -row["your_pages"], row["term"]))
    return rows[:limit]


# ----------------------------------------------------------------- the report


def summarise(mine_report: dict[str, Any], competitor_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """The comparison, as the API returns it."""
    if not competitor_reports:
        raise NoComparison("a comparison needs at least one competitor crawl")

    mine = build_site(mine_report)
    others = [build_site(report) for report in competitor_reports]

    my_profile = profile(mine)
    their_profiles = [profile(site) for site in others]
    comparable = [p for p in their_profiles if p["comparable"]]

    verdicts = rate_verdicts(my_profile, their_profiles)

    return {
        "you": my_profile,
        "competitors": their_profiles,
        # Named rather than counted: "2 of 4 competitors were too small to
        # compare" is a sentence a reader can check, and dropping them silently
        # would make the verdicts look better founded than they are.
        "ignored": [p["label"] for p in their_profiles if not p["comparable"]],
        "compared_against": len(comparable),
        "verdicts": verdicts,
        "behind_on": [v["metric"] for v in verdicts if v["verdict"] == "behind"],
        "ahead_on": [v["metric"] for v in verdicts if v["verdict"] == "ahead"],
        "content_length": length_verdict(my_profile, their_profiles),
        "missing_themes": themes(mine, others),
        "shared_themes": shared_themes(mine, others),
        # Said in the report because it is the limit a reader would otherwise
        # not think of: none of this compares how big the sites are.
        "method": (
            "مقایسه فقط روی نرخ‌ها و میانه‌هاست، نه تعداد صفحه‌ها — هر خزش سقف "
            f"دارد و شمردن صفحه‌ها اندازه‌ی سقف را می‌سنجد نه اندازه‌ی سایت. "
            f"سایتی با کمتر از {MIN_SAMPLE} صفحه‌ی ایندکس‌پذیر در داوری‌ها "
            f"شرکت داده نمی‌شود، و یک عبارت وقتی «موضوع» است که دست‌کم "
            f"{MIN_COMPETITORS} رقیب رویش نوشته باشند."
        ),
    }
