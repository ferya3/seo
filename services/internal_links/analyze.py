"""What a site's own links say about it.

The crawl already reports orphan pages, deep pages and generic anchors as
issues. This is the thing rules cannot do: reason about the graph as a graph.
A rule can see that a page has one incoming link; only the graph can say that
the site's own structure treats the contact page as its most important asset
while the article that took a week to write sits on the edge of it.

Everything here is a pure function of an edge list plus a page list, which is
why this file is separate from the service around it: no network, no database,
no clock. The whole judgement of the service is testable from a dictionary.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urlsplit, urlunsplit

# How much of a page's authority flows onward, the standard damping factor. The
# remainder is spread evenly, which is what keeps a page with no incoming links
# from scoring zero and disappearing from the ranking entirely.
DAMPING = 0.85
ITERATIONS = 40
CONVERGENCE = 1e-9

# Anchors that describe the click rather than the destination. Same list the
# engine's rules use, kept here because this service must run without importing
# the engine.
GENERIC_ANCHORS = {
    "اینجا", "کلیک کنید", "کلیک", "بیشتر", "ادامه مطلب", "ادامه", "لینک", "این لینک",
    "بیشتر بخوانید", "مشاهده", "جزئیات", "اطلاعات بیشتر", "دانلود",
    "click here", "here", "read more", "more", "link", "this", "learn more",
    "details", "download", "continue", "see more",
}


@dataclass
class Page:
    url: str
    title: str | None = None
    status: int = 200
    indexable: bool = True
    depth: int = 0
    words: int = 0

    @property
    def reachable(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class Edge:
    source: str
    target: str
    anchor: str = ""
    nofollow: bool = False


@dataclass
class Graph:
    pages: dict[str, Page] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    @property
    def urls(self) -> list[str]:
        return list(self.pages)


def normalise(url: str) -> str:
    """One spelling per page, so `/a`, `/a#top` and `/a?` are one node.

    Without this the graph counts a page twice and reports it as an orphan
    linking to itself.
    """
    url, _ = urldefrag(url.strip())
    parts = urlsplit(url)
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def build(report: dict[str, Any]) -> Graph:
    """Read a crawl report into a graph.

    Edges pointing outside the crawled set are kept, because "this link points
    at a page that was never crawled" is itself a finding — the target simply
    has no Page.
    """
    graph = Graph()
    for row in report.get("pages") or []:
        url = normalise(str(row.get("url") or ""))
        if not url:
            continue
        graph.pages[url] = Page(
            url=url,
            title=row.get("title"),
            status=int(row.get("status") or 0),
            indexable=bool(row.get("indexable")),
            depth=int(row.get("depth") or 0),
            words=int(row.get("words") or 0),
        )

    for row in (report.get("links") or {}).get("edges") or []:
        source, target = normalise(str(row.get("from") or "")), normalise(str(row.get("to") or ""))
        # A page linking to itself adds nothing and would inflate its own
        # authority; every navigation bar has one.
        if not source or not target or source == target:
            continue
        graph.edges.append(Edge(
            source=source,
            target=target,
            anchor=(row.get("anchor") or "").strip(),
            nofollow=bool(row.get("nofollow")),
        ))
    return graph


# ------------------------------------------------------------------ authority


def authority(graph: Graph) -> dict[str, float]:
    """Internal PageRank, normalised so the best page scores 100.

    The absolute numbers mean nothing; the ordering is the point. Only edges
    between crawled pages count — a link to a page that was never crawled
    cannot pass authority to something we know nothing about.

    Nofollow links are followed here on purpose. Google stopped treating
    nofollow as a directive in 2019, and a site that nofollows its own internal
    links is describing its structure either way.
    """
    urls = graph.urls
    if not urls:
        return {}

    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.target in graph.pages:
            outgoing[edge.source].append(edge.target)

    count = len(urls)
    rank = dict.fromkeys(urls, 1.0 / count)

    for _ in range(ITERATIONS):
        leaked = sum(rank[url] for url in urls if not outgoing.get(url))
        nextrank = dict.fromkeys(urls, (1.0 - DAMPING) / count + DAMPING * leaked / count)

        for source, targets in outgoing.items():
            if source not in rank:
                continue
            share = DAMPING * rank[source] / len(targets)
            for target in targets:
                nextrank[target] += share

        if sum(abs(nextrank[u] - rank[u]) for u in urls) < CONVERGENCE:
            rank = nextrank
            break
        rank = nextrank

    top = max(rank.values()) or 1.0
    return {url: round(value / top * 100, 1) for url, value in rank.items()}


# ------------------------------------------------------------------- findings


def inlink_counts(graph: Graph) -> Counter[str]:
    """Distinct linking pages, not link tags.

    A navigation link repeated in a header and a footer is one page's vote,
    counted twice by anything that counts tags.
    """
    seen: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    for edge in graph.edges:
        key = (edge.source, edge.target)
        if key in seen:
            continue
        seen.add(key)
        counts[edge.target] += 1
    return counts


def orphans(graph: Graph, start_url: str | None = None) -> list[str]:
    counts = inlink_counts(graph)
    start = normalise(start_url) if start_url else None
    return sorted(
        url for url, page in graph.pages.items()
        if page.indexable and counts[url] == 0 and url != start
    )


def dead_ends(graph: Graph) -> list[str]:
    """Pages that receive links and give none back.

    Not an error — a checkout page should be a dead end — but on an article it
    means the reader arrives and has nowhere to go.
    """
    with_outgoing = {edge.source for edge in graph.edges}
    return sorted(
        url for url, page in graph.pages.items()
        if page.indexable and url not in with_outgoing
    )


def broken_targets(graph: Graph) -> list[dict[str, Any]]:
    """Internal links pointing at pages that answered with an error.

    Every one of these is a link the site is spending on nothing.
    """
    counts: Counter[str] = Counter()
    for edge in graph.edges:
        page = graph.pages.get(edge.target)
        if page is not None and not page.reachable:
            counts[edge.target] += 1

    return [
        {"url": url, "status": graph.pages[url].status, "linked_from": count}
        for url, count in counts.most_common(50)
    ]


def links_to_noindex(graph: Graph) -> list[dict[str, Any]]:
    """Links to pages that were fetched fine but are marked noindex.

    Common and usually deliberate (a login page in the footer), so this is
    reported as a fact to look at rather than as a fault.
    """
    counts: Counter[str] = Counter()
    for edge in graph.edges:
        page = graph.pages.get(edge.target)
        if page is not None and page.reachable and not page.indexable:
            counts[edge.target] += 1

    return [{"url": url, "linked_from": count} for url, count in counts.most_common(25)]


def anchor_profile(graph: Graph, url: str) -> dict[str, Any]:
    """What the site calls a page when it links to it."""
    anchors = [e.anchor for e in graph.edges if e.target == url and e.anchor]
    counted = Counter(anchors)
    generic = sum(n for text, n in counted.items() if text.strip().lower() in GENERIC_ANCHORS)
    empty = sum(1 for e in graph.edges if e.target == url and not e.anchor)

    return {
        "url": url,
        "variants": [{"anchor": text, "count": n} for text, n in counted.most_common(10)],
        "generic": generic,
        "empty": empty,
        "total": len(anchors) + empty,
    }


def undescribed(graph: Graph, minimum: int = 2) -> list[dict[str, Any]]:
    """Pages whose incoming links never say what they are about.

    A page linked five times as «اینجا» has five links and no keyword signal;
    that is a rewrite worth doing, and it is invisible to a per-page rule.
    """
    out: list[dict[str, Any]] = []
    for url in {e.target for e in graph.edges if e.target in graph.pages}:
        profile = anchor_profile(graph, url)
        described = profile["total"] - profile["generic"] - profile["empty"]
        if profile["total"] >= minimum and described == 0:
            out.append(profile)
    return sorted(out, key=lambda p: -p["total"])[:25]


def overlooked(
    graph: Graph, ranking: dict[str, float], start_url: str | None = None, limit: int = 15
) -> list[dict[str, Any]]:
    """Substantial pages the site's own linking treats as unimportant.

    The one finding here that names a specific piece of work: these pages have
    real content and almost no internal support, so a link from a strong page
    is the cheapest thing that can be done for them.

    Three conditions, and all three are needed. Content above the median, so
    this is not a list of thin pages. Two incoming links or fewer, so the page
    really is unsupported. And authority below the middle of the site, because
    a page every strong page links to is not overlooked no matter how few
    distinct linkers it has.
    """
    counts = inlink_counts(graph)
    start = normalise(start_url) if start_url else None

    words = [p.words for p in graph.pages.values() if p.indexable and p.words] or [0]
    median_words = sorted(words)[len(words) // 2]
    scores = sorted(ranking.values()) or [0.0]
    median_score = scores[len(scores) // 2]

    candidates = [
        {
            "url": url,
            "title": page.title,
            "words": page.words,
            "inlinks": counts[url],
            "depth": page.depth,
            "authority": ranking.get(url, 0.0),
        }
        for url, page in graph.pages.items()
        # The start URL is the root of the site, not a page waiting for a link.
        if page.indexable and page.reachable and url != start
        and page.words >= median_words and counts[url] <= 2
        and ranking.get(url, 0.0) <= median_score
    ]
    # Weakest support first, and among equals the page with the most content —
    # that is the one losing the most by being ignored.
    return sorted(candidates, key=lambda row: (row["authority"], -row["words"]))[:limit]


def hubs(graph: Graph, ranking: dict[str, float], limit: int = 10) -> list[dict[str, Any]]:
    """The pages worth linking *from*: high authority, plenty of outgoing links."""
    outgoing = Counter(e.source for e in graph.edges)
    rows = [
        {
            "url": url,
            "title": page.title,
            "authority": ranking.get(url, 0.0),
            "outgoing": outgoing[url],
        }
        for url, page in graph.pages.items() if page.indexable
    ]
    return sorted(rows, key=lambda row: -row["authority"])[:limit]


def summarise(report: dict[str, Any]) -> dict[str, Any]:
    """The whole analysis, from one crawl report."""
    graph = build(report)
    ranking = authority(graph)
    counts = inlink_counts(graph)
    start_url = report.get("start_url")

    indexable = [p for p in graph.pages.values() if p.indexable]
    depths = [p.depth for p in indexable]
    inlink_values = [counts[url] for url, p in graph.pages.items() if p.indexable]

    return {
        "pages": len(graph.pages),
        "indexable_pages": len(indexable),
        "internal_links": len(graph.edges),
        "truncated": bool((report.get("links") or {}).get("truncated")),
        "average_inlinks": round(sum(inlink_values) / len(inlink_values), 1) if inlink_values else 0,
        "max_depth": max(depths) if depths else 0,
        # Depth is what decides whether a page gets crawled often. A count per
        # level says more about a site's shape than any average.
        "depth_histogram": dict(sorted(Counter(depths).items())),
        "orphans": orphans(graph, start_url),
        "dead_ends": dead_ends(graph),
        "broken_targets": broken_targets(graph),
        "links_to_noindex": links_to_noindex(graph),
        "undescribed": undescribed(graph),
        "hubs": hubs(graph, ranking),
        "overlooked": overlooked(graph, ranking, start_url),
        "nofollow_internal": sum(1 for e in graph.edges if e.nofollow),
    }
