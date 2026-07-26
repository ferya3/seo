"""Keyword expansion, scoring, intent classification and clustering.

Network sources are stubbed — these tests cover our logic, not Google's uptime.
"""

from __future__ import annotations

import pytest

from seoagent.config import KeywordConfig
from seoagent.export import keywords_markdown
from seoagent.keywords import analyze
from seoagent.keywords import research as research_module
from seoagent.keywords import sources as source_module
from seoagent.keywords.expand import build_queries
from seoagent.keywords.sources import Suggestion

# ----------------------------------------------------------------- expansion


def test_persian_expansion_puts_question_words_after_the_seed():
    queries = build_queries("سئو", lang="fa")
    assert "سئو چیست" in queries
    assert "سئو چگونه" in queries
    assert "سئو قیمت" in queries
    assert "قیمت سئو" in queries
    assert "سئو نزدیک من" in queries


def test_english_expansion_puts_question_words_before_the_seed():
    queries = build_queries("seo", lang="en")
    assert "how to seo" in queries
    assert "what is seo" in queries
    assert "seo price" in queries


def test_alphabet_soup_can_be_switched_off():
    with_alphabet = build_queries("سئو", lang="fa", include_alphabet=True)
    without = build_queries("سئو", lang="fa", include_alphabet=False)
    assert len(with_alphabet) > len(without)
    assert "سئو ا" in with_alphabet
    assert "سئو ا" not in without


def test_queries_are_deduplicated_and_seed_is_first():
    queries = build_queries("سئو", lang="fa")
    assert queries[0] == "سئو"
    assert len(queries) == len(set(queries))


def test_empty_seed_produces_no_queries():
    assert build_queries("   ") == []


# --------------------------------------------------------------------- intent


@pytest.mark.parametrize(
    "keyword,expected",
    [
        ("خرید کفش ورزشی", "transactional"),
        ("بهترین کفش ورزشی", "commercial"),
        ("کفش ورزشی چیست", "informational"),
        ("کفش ورزشی در تهران", "local"),
        ("buy running shoes", "transactional"),
        ("best running shoes", "commercial"),
        ("how to clean running shoes", "informational"),
        ("running shoes near me", "local"),
    ],
)
def test_intent_classification(keyword, expected):
    assert analyze.classify_intent(keyword) == expected


def test_brand_terms_are_navigational():
    assert analyze.classify_intent("دیجیکالا کفش", brand_terms={"دیجیکالا"}) == "navigational"


# -------------------------------------------------------------------- scoring


def test_demand_rewards_multiple_sources_and_top_position():
    strong = analyze.Keyword("الف", sources={"google", "bing", "youtube", "duckduckgo"}, hits=10, best_position=0)
    weak = analyze.Keyword("ب", sources={"google"}, hits=1, best_position=9)
    assert strong.demand_score > weak.demand_score
    assert 0 <= weak.demand_score <= 100
    assert 0 <= strong.demand_score <= 100


def test_opportunity_favours_long_tail_over_head_terms():
    head = analyze.Keyword("سئو", sources={"google", "bing"}, hits=8, best_position=0)
    tail = analyze.Keyword("آموزش سئو تکنیکال برای سایت فروشگاهی", sources={"google", "bing"}, hits=8, best_position=0)
    assert tail.opportunity_score > head.opportunity_score
    assert tail.is_long_tail and not head.is_long_tail


def test_scores_stay_within_bounds_for_extreme_input():
    extreme = analyze.Keyword("الف " * 30, sources={"a", "b", "c", "d", "e"}, hits=999, best_position=0)
    assert 0 <= extreme.demand_score <= 100
    assert 0 <= extreme.opportunity_score <= 100


# ----------------------------------------------------------------- clustering


def test_clustering_groups_by_distinctive_token():
    keywords = [
        analyze.Keyword("قیمت کفش ورزشی", hits=5, sources={"google"}),
        analyze.Keyword("قیمت کفش مردانه", hits=4, sources={"google"}),
        analyze.Keyword("خرید کفش ورزشی", hits=3, sources={"google"}),
        analyze.Keyword("خرید کفش مردانه", hits=2, sources={"google"}),
    ]
    for keyword in keywords:
        keyword.intent = analyze.classify_intent(keyword.keyword)

    clusters = analyze.cluster(keywords, "کفش")
    assert clusters
    labels = {c["label"] for c in clusters}
    assert labels & {"قیمت", "خرید", "ورزشی", "مردانه"}
    assert sum(c["size"] for c in clusters) == len(keywords)
    for item in clusters:
        assert item["primary"]
        assert item["intent_fa"]


def test_content_plan_maps_intent_to_page_type():
    keywords = [analyze.Keyword("خرید کفش ورزشی", hits=5, sources={"google"}, intent="transactional")]
    clusters = analyze.cluster(keywords, "کفش")
    plan = analyze.content_plan(clusters)
    assert plan[0]["page_type"] == "صفحه‌ی محصول یا خدمت"
    assert plan[0]["suggested_title"]


# ------------------------------------------------------- research (stubbed)


@pytest.fixture
def stub_sources(monkeypatch):
    """Deterministic fake autocomplete so the pipeline is testable offline."""

    def fake(name: str):
        def call(query: str, lang: str = "fa", country: str = "IR", timeout: float = 12.0):
            base = query.strip()
            return [
                Suggestion(f"{base} راهنما", name, 0),
                Suggestion(f"{base} قیمت", name, 1),
                Suggestion(base, name, 2),
            ]

        return call

    monkeypatch.setattr(
        source_module, "SOURCES", {n: fake(n) for n in ("google", "youtube", "bing", "duckduckgo")}
    )
    monkeypatch.setattr(source_module, "trending_now", lambda *a, **k: ["ترند یک", "ترند دو"])
    monkeypatch.setattr(source_module, "related_from_wikipedia", lambda *a, **k: ["موجودیت"])


def test_research_pipeline_end_to_end(stub_sources):
    report = research_module.research(
        KeywordConfig(seed="کفش", include_alphabet=False, include_comparisons=False, delay=0, max_keywords=50)
    )
    assert report.keywords
    assert report.queries_sent > 0
    assert report.clusters
    assert report.plan
    assert report.trending == ["ترند یک", "ترند دو"]
    assert report.entities == ["موجودیت"]
    assert not report.errors

    payload = report.to_dict()
    assert payload["total"] == len(report.keywords)
    assert set(payload["by_intent"]) <= {
        "informational", "commercial", "transactional", "local", "navigational"
    }

    markdown = keywords_markdown(payload)
    assert "# تحقیق کلمات کلیدی" in markdown
    assert "## کلمات کلیدی" in markdown


def test_research_records_source_failures(monkeypatch):
    def broken(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(source_module, "SOURCES", {"google": broken})
    monkeypatch.setattr(source_module, "trending_now", lambda *a, **k: [])
    monkeypatch.setattr(source_module, "related_from_wikipedia", lambda *a, **k: [])

    report = research_module.research(
        KeywordConfig(seed="کفش", sources=["google"], include_alphabet=False, delay=0)
    )
    assert report.errors
    assert not report.keywords


def test_empty_seed_is_rejected():
    report = research_module.research(KeywordConfig(seed="  "))
    assert report.errors
    assert not report.keywords
