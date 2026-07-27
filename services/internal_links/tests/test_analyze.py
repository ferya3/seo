"""The link graph, reasoned about as a graph.

No network, no database, no browser: a crawl report goes in and findings come
out, so every judgement this service makes is pinned here.

    pytest services/internal_links
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.internal_links import analyze  # noqa: E402


def report(pages, edges, start_url="https://site.test/", truncated=False):
    return {
        "start_url": start_url,
        "pages": [
            {"url": url, "title": title, "status": status, "indexable": indexable,
             "depth": depth, "words": words}
            for url, title, status, indexable, depth, words in pages
        ],
        "links": {
            "edges": [
                {"from": source, "to": target, "anchor": anchor, "nofollow": nofollow}
                for source, target, anchor, nofollow in edges
            ],
            "truncated": truncated,
        },
    }


HOME = "https://site.test/"
BLOG = "https://site.test/blog"
POST = "https://site.test/blog/post"
ABOUT = "https://site.test/about"

SIMPLE = report(
    pages=[
        (HOME, "خانه", 200, True, 0, 300),
        (BLOG, "وبلاگ", 200, True, 1, 400),
        (POST, "مقاله", 200, True, 2, 1200),
        (ABOUT, "درباره ما", 200, True, 1, 150),
    ],
    edges=[
        (HOME, BLOG, "وبلاگ", False),
        (HOME, ABOUT, "درباره ما", False),
        (BLOG, POST, "مقاله‌ی کامل", False),
        (POST, BLOG, "بازگشت", False),
    ],
)


# ------------------------------------------------------------------- the graph


def test_a_url_has_one_spelling():
    """`/a`, `/a/` and `/a#top` are one page. Counted separately, a page turns
    up as an orphan that links to itself."""
    assert analyze.normalise("https://Site.test/a/") == analyze.normalise("https://site.test/a")
    assert analyze.normalise("https://site.test/a#top") == analyze.normalise("https://site.test/a")
    assert analyze.normalise("https://site.test") == "https://site.test/"


def test_a_query_string_is_a_different_page():
    # ?page=2 is genuinely different content; folding it in would merge a
    # paginated archive into one node.
    assert analyze.normalise("https://site.test/a?p=2") != analyze.normalise("https://site.test/a")


def test_self_links_are_dropped():
    graph = analyze.build(report(
        pages=[(HOME, "خانه", 200, True, 0, 100)],
        edges=[(HOME, HOME, "خانه", False), (HOME, HOME + "#top", "بالا", False)],
    ))
    assert graph.edges == []


def test_a_link_to_an_uncrawled_page_is_kept():
    """"This links somewhere we never saw" is a finding, not noise to discard."""
    graph = analyze.build(report(
        pages=[(HOME, "خانه", 200, True, 0, 100)],
        edges=[(HOME, "https://site.test/ghost", "شبح", False)],
    ))
    assert len(graph.edges) == 1
    assert "https://site.test/ghost" not in graph.pages


# ----------------------------------------------------------------- authority


def test_the_most_linked_page_scores_highest():
    ranking = analyze.authority(analyze.build(SIMPLE))
    assert ranking[BLOG] == 100.0            # linked from home and from the post
    assert ranking[ABOUT] < ranking[BLOG]


def test_every_page_gets_a_score_even_with_no_links_in():
    """A zero would drop the page out of the ordering entirely, which is the
    opposite of what a report about neglected pages should do."""
    ranking = analyze.authority(analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (ABOUT, None, 200, True, 1, 100)],
        edges=[],
    )))
    assert all(score > 0 for score in ranking.values())


def test_authority_does_not_leak_out_of_the_site():
    """A page whose only link points outside the crawl must not take its
    authority with it — otherwise the totals shrink every iteration."""
    ranking = analyze.authority(analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (ABOUT, None, 200, True, 1, 100)],
        edges=[(HOME, "https://elsewhere.test/x", "بیرون", False)],
    )))
    assert ranking[ABOUT] > 0


def test_an_empty_graph_does_not_divide_by_zero():
    assert analyze.authority(analyze.build(report(pages=[], edges=[]))) == {}


# ------------------------------------------------------------------ findings


def test_a_page_nothing_links_to_is_an_orphan():
    graph = analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (ABOUT, None, 200, True, 1, 100)],
        edges=[],
    ))
    # The start URL is reachable by definition; calling it an orphan is noise.
    assert analyze.orphans(graph, HOME) == [analyze.normalise(ABOUT)]


def test_the_same_link_in_a_header_and_a_footer_is_one_vote():
    """Counting tags instead of linking pages makes every navigation item look
    like the best-supported page on the site."""
    graph = analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (ABOUT, None, 200, True, 1, 100)],
        edges=[(HOME, ABOUT, "درباره", False), (HOME, ABOUT, "درباره ما", False)],
    ))
    assert analyze.inlink_counts(graph)[analyze.normalise(ABOUT)] == 1


def test_a_page_with_no_way_out_is_a_dead_end():
    graph = analyze.build(SIMPLE)
    assert analyze.normalise(ABOUT) in analyze.dead_ends(graph)
    assert analyze.normalise(POST) not in analyze.dead_ends(graph)


def test_links_to_a_broken_page_are_counted():
    graph = analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (ABOUT, None, 404, False, 1, 0)],
        edges=[(HOME, ABOUT, "درباره", False), (BLOG, ABOUT, "درباره", False)],
    ))
    broken = analyze.broken_targets(graph)
    assert broken[0]["status"] == 404
    assert broken[0]["linked_from"] == 2


def test_a_noindex_target_is_reported_apart_from_a_broken_one():
    """Both waste internal links, but one is a bug and the other is usually a
    deliberate login page in the footer."""
    graph = analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (ABOUT, None, 200, False, 1, 100)],
        edges=[(HOME, ABOUT, "ورود", False)],
    ))
    assert analyze.broken_targets(graph) == []
    assert analyze.links_to_noindex(graph)[0]["url"] == analyze.normalise(ABOUT)


def test_a_page_linked_only_as_click_here_is_flagged():
    graph = analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (BLOG, None, 200, True, 1, 100),
               (ABOUT, None, 200, True, 1, 100)],
        edges=[(HOME, ABOUT, "اینجا", False), (BLOG, ABOUT, "click here", False)],
    ))
    flagged = analyze.undescribed(graph)
    assert flagged[0]["url"] == analyze.normalise(ABOUT)
    assert flagged[0]["generic"] == 2


def test_a_page_with_one_real_anchor_is_not_flagged():
    graph = analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (BLOG, None, 200, True, 1, 100),
               (ABOUT, None, 200, True, 1, 100)],
        edges=[(HOME, ABOUT, "اینجا", False), (BLOG, ABOUT, "درباره‌ی تیم ما", False)],
    ))
    assert analyze.undescribed(graph) == []


def test_an_empty_anchor_counts_as_undescribed():
    # An image link with no alt reaches the graph as an empty anchor.
    graph = analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (BLOG, None, 200, True, 1, 100),
               (ABOUT, None, 200, True, 1, 100)],
        edges=[(HOME, ABOUT, "", False), (BLOG, ABOUT, "", False)],
    ))
    assert analyze.undescribed(graph)[0]["empty"] == 2


def test_a_long_page_with_no_support_is_the_first_opportunity():
    graph = analyze.build(report(
        pages=[
            (HOME, None, 200, True, 0, 200),
            (BLOG, None, 200, True, 1, 200),
            (POST, "مقاله‌ی مفصل", 200, True, 3, 2000),
        ],
        edges=[(HOME, BLOG, "وبلاگ", False), (BLOG, HOME, "خانه", False)],
    ))
    first = analyze.overlooked(graph, analyze.authority(graph), HOME)[0]
    assert first["url"] == analyze.normalise(POST)
    assert first["inlinks"] == 0


def test_a_well_supported_page_is_not_an_opportunity():
    graph = analyze.build(SIMPLE)
    urls = [row["url"] for row in analyze.overlooked(graph, analyze.authority(graph), HOME)]
    assert analyze.normalise(BLOG) not in urls    # two pages link to it


def test_a_noindex_page_is_not_an_opportunity():
    """There is no point spending a link on a page nobody is allowed to index."""
    graph = analyze.build(report(
        pages=[(HOME, None, 200, True, 0, 100), (POST, None, 200, False, 2, 3000)],
        edges=[],
    ))
    assert analyze.overlooked(graph, analyze.authority(graph), HOME) == []


# ------------------------------------------------------------------- summary


def test_the_summary_counts_what_it_says_it_counts():
    result = analyze.summarise(SIMPLE)
    assert result["pages"] == 4
    assert result["indexable_pages"] == 4
    assert result["internal_links"] == 4
    assert result["max_depth"] == 2
    assert result["depth_histogram"] == {0: 1, 1: 2, 2: 1}


def test_the_summary_says_when_the_graph_was_cut_short():
    """A partial graph makes every count a lower bound. Reporting it as final
    would turn a truncated crawl into a wrong answer about orphan pages."""
    assert analyze.summarise(report(pages=[], edges=[], truncated=True))["truncated"] is True


def test_a_report_with_no_link_section_still_analyses():
    """Reports written before the crawl recorded edges have no `links` key.
    Reading one should produce an empty graph, not an exception."""
    result = analyze.summarise({"pages": [{"url": HOME, "status": 200, "indexable": True}]})
    assert result["internal_links"] == 0
    assert result["pages"] == 1


def test_an_entirely_empty_report_is_survivable():
    result = analyze.summarise({})
    assert result["pages"] == 0
    assert result["average_inlinks"] == 0
    assert result["hubs"] == []


def test_nofollow_internal_links_are_counted_but_still_followed():
    """Google stopped treating nofollow as a directive in 2019, so dropping
    those edges would describe a structure the site does not have. Counting
    them keeps the fact visible."""
    graph_report = report(
        pages=[(HOME, None, 200, True, 0, 100), (ABOUT, None, 200, True, 1, 100)],
        edges=[(HOME, ABOUT, "درباره", True)],
    )
    result = analyze.summarise(graph_report)
    assert result["nofollow_internal"] == 1
    assert result["orphans"] == []
