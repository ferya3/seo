"""What a page of search results means for a particular site.

Everything here is a pure function of a result list, which is why it is
separate from providers.py: the fetching cannot be verified from this
environment, but the reasoning applied to what comes back can be, completely.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .providers import Result

# Rough click-through by organic position. Public studies disagree on the exact
# numbers, so these are used only to rank opportunities against each other,
# never reported as a traffic forecast — an estimate dressed up as a prediction
# is worse than no number at all.
CTR_BY_POSITION = {
    1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.06,
    6: 0.05, 7: 0.04, 8: 0.03, 9: 0.03, 10: 0.02,
}


@dataclass
class Ranking:
    keyword: str
    position: int | None                    # None means not in the fetched page
    url: str | None = None
    competitors_above: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "position": self.position,
            "url": self.url,
            "competitors_above": self.competitors_above,
            "opportunity": opportunity(self.position),
            "results": self.results,
            "error": self.error,
        }


def rank(keyword: str, results: list[Result], target_domain: str | None) -> Ranking:
    """Find the target's best position and who sits above it."""
    target = (target_domain or "").lower().removeprefix("www.")
    ordered = sorted(results, key=lambda r: r.position)

    mine = next((r for r in ordered if target and _same_site(r.domain, target)), None)
    above = [r.domain for r in ordered if mine is not None and r.position < mine.position]

    return Ranking(
        keyword=keyword,
        position=mine.position if mine else None,
        url=mine.url if mine else None,
        # Deduplicated, order kept: a competitor holding three of the top five
        # is one competitor to beat, not three, but which one is first matters.
        competitors_above=list(dict.fromkeys(above)),
        results=[r.to_dict() for r in ordered],
    )


def _same_site(candidate: str, target: str) -> bool:
    """`example.com` matches `blog.example.com`, but not `notexample.com`.

    Suffix matching alone would make "example.com" match "notexample.com",
    which would silently report a competitor's ranking as your own.
    """
    return candidate == target or candidate.endswith(f".{target}")


def opportunity(position: int | None) -> int:
    """0-100, higher meaning more worth working on.

    Not in the top ten scores highest: the work is unstarted, so the whole
    click-through is still on the table. Position one scores lowest — there is
    nowhere left to climb. Position two through five score high because they
    are the cheapest real wins, which is the judgement this number exists to
    make.
    """
    if position is None:
        return 100
    if position == 1:
        return 0
    gain = CTR_BY_POSITION.get(1, 0.28) - CTR_BY_POSITION.get(position, 0.01)
    return max(0, min(100, round(gain * 100 / 0.28 * 0.9)))


def summarise(rankings: list[Ranking], target_domain: str | None) -> dict[str, Any]:
    """The report: where the site stands, and what to do next."""
    ranked = [r for r in rankings if r.position is not None]
    missing = [r for r in rankings if r.position is None and r.error is None]

    positions = [r.position for r in ranked]
    competitors = Counter(
        domain for r in rankings for domain in r.competitors_above
        if not (target_domain and _same_site(domain, target_domain.lower()))
    )

    return {
        "target_domain": target_domain,
        "keywords_checked": len(rankings),
        "keywords_ranked": len(ranked),
        "keywords_missing": len(missing),
        "average_position": round(sum(positions) / len(positions), 1) if positions else None,
        "best": _best(ranked),
        # Who to study. A domain that outranks you on many terms is a
        # competitor; one that beats you on a single term is noise.
        "top_competitors": [
            {"domain": domain, "outranks_on": count}
            for domain, count in competitors.most_common(10)
        ],
        # Ordered by how much there is to gain, so the first row is the work to
        # do first rather than the alphabetically luckiest keyword.
        "opportunities": sorted(
            ({"keyword": r.keyword, "position": r.position, "opportunity": opportunity(r.position)}
             for r in rankings if r.error is None),
            key=lambda item: (-item["opportunity"], item["keyword"]),
        )[:25],
        "failures": [
            {"keyword": r.keyword, "error": r.error} for r in rankings if r.error is not None
        ],
    }


def _best(ranked: list[Ranking]) -> dict[str, Any] | None:
    if not ranked:
        return None
    best = min(ranked, key=lambda r: r.position)
    return {"keyword": best.keyword, "position": best.position, "url": best.url}
