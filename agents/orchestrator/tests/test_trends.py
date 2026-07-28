"""What changed since the last run.

Every judgement here is one that produces a plausible-looking wrong answer
when it is missing: a metric where up is bad reported as an improvement, a
step that did not run last time reported as a jump from zero, a crawl of ten
pages compared with a crawl of a hundred.

    pytest agents/orchestrator/tests/test_trends.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agents.orchestrator import trends  # noqa: E402


def report(pages: int = 40, **headline) -> dict:
    return {"crawl": {"pages_crawled": pages}, "headline": headline}


def by_metric(trend: dict) -> dict:
    return {c["metric"]: c for c in trend["changes"]}


def test_a_first_run_has_no_trend_rather_than_an_empty_one():
    # "Nothing to compare with" and "nothing changed" are different answers,
    # and a section of zeros reads as the second.
    assert trends.compare(None, report(overall_score=70)) is None
    assert trends.compare({}, report(overall_score=70)) is None


def test_up_is_not_the_same_as_better():
    trend = trends.compare(
        report(overall_score=60, total_issues=10),
        report(overall_score=70, total_issues=20),
    )
    changes = by_metric(trend)

    assert changes["overall_score"]["direction"] == "better"
    # More issues is worse, however the sign falls out.
    assert changes["total_issues"]["direction"] == "worse"
    assert trend["better"] == ["overall_score"]
    assert trend["worse"] == ["total_issues"]


def test_a_lower_average_position_is_an_improvement():
    trend = trends.compare(report(average_position=8.0), report(average_position=3.0))
    assert by_metric(trend)["average_position"]["direction"] == "better"


def test_a_metric_missing_on_either_side_is_not_compared():
    # The rank check was skipped last week and ran this week. "0 → 5" would be
    # a claim about the site; the truth is only that the step ran.
    trend = trends.compare(
        report(overall_score=70, keywords_ranked=None),
        report(overall_score=70, keywords_ranked=5),
    )
    assert "keywords_ranked" not in by_metric(trend)

    trend = trends.compare(report(keywords_ranked=5), report(keywords_ranked=None))
    assert "keywords_ranked" not in by_metric(trend)


def test_zero_is_a_measurement_and_is_still_compared():
    # The mirror of the test above: 0 is a number the run produced, unlike
    # None, and a site that went from three orphan pages to none has news.
    trend = trends.compare(report(orphan_pages=3), report(orphan_pages=0))
    assert by_metric(trend)["orphan_pages"]["direction"] == "better"


def test_a_small_move_is_not_news():
    trend = trends.compare(report(overall_score=71), report(overall_score=72))
    change = by_metric(trend)["overall_score"]

    assert change["direction"] == "level"
    assert change["change"] == 1        # still reported, just not as movement
    assert trend["better"] == [] and trend["worse"] == []


def test_counting_metrics_are_dropped_when_the_crawls_were_different_sizes():
    # Ten pages against a hundred: fewer issues because fewer pages were
    # looked at, which is not the site improving.
    trend = trends.compare(
        report(pages=100, overall_score=70, total_issues=50, orphan_pages=8),
        report(pages=10, overall_score=75, total_issues=5, orphan_pages=1),
    )
    changes = by_metric(trend)

    assert "total_issues" not in changes
    assert "orphan_pages" not in changes
    # The score is a rate out of a hundred, so it survives the size change.
    assert changes["overall_score"]["direction"] == "better"
    assert trend["comparable_sample"] is False
    assert "مقایسه نشدند" in trend["note"]


def test_a_crawl_that_grew_a_little_is_still_comparable():
    trend = trends.compare(
        report(pages=40, total_issues=20),
        report(pages=45, total_issues=12),
    )
    assert trend["comparable_sample"] is True
    assert by_metric(trend)["total_issues"]["direction"] == "better"


def test_two_empty_crawls_are_not_a_comparable_sample():
    trend = trends.compare(report(pages=0, total_issues=0), report(pages=0, total_issues=0))
    assert trend["comparable_sample"] is False


def test_the_trend_names_the_run_it_compared_against():
    previous = {**report(overall_score=60), "workflow_id": "w-1",
                "finished_at": "2026-07-20T09:00:00+00:00"}
    trend = trends.compare(previous, report(overall_score=70))

    assert trend["compared_with"] == "w-1"
    assert trend["compared_at"] == "2026-07-20T09:00:00+00:00"


# ------------------------------------------------------------- the sentence


def test_the_sentence_leads_with_the_score_when_it_moved():
    trend = trends.compare(report(overall_score=45), report(overall_score=58))
    line = trends.headline(trend)

    assert "45" in line and "58" in line and "بالا رفته" in line


def test_the_sentence_says_level_rather_than_nothing():
    # Silence would read as "the comparison did not happen".
    trend = trends.compare(report(overall_score=70), report(overall_score=70))
    assert "تغییر معناداری نیست" in trends.headline(trend)


def test_no_sentence_at_all_on_a_first_run():
    assert trends.headline(None) is None
    assert trends.headline({"changes": []}) is None


def test_the_sentence_counts_both_directions_when_both_moved():
    trend = trends.compare(
        report(overall_score=60, keyword_coverage=80),
        report(overall_score=75, keyword_coverage=40),
    )
    line = trends.headline(trend)
    assert "بهتر" in line and "بدتر" in line


# ------------------------------------------------------------- categories


def with_categories(pages: int = 40, scores: dict | None = None, **headline) -> dict:
    return {
        "crawl": {
            "pages_crawled": pages,
            "category_scores": [
                {"category": key, "label_fa": label, "score": score}
                for key, (label, score) in (scores or {}).items()
            ],
        },
        "headline": headline,
    }


def test_a_category_moving_is_reported_even_when_the_headline_did_not():
    # The live case this exists for: every page gained a meta description, the
    # overall score landed on the same number, and the report said "nothing
    # changed".
    trend = trends.compare(
        with_categories(overall_score=45, scores={"content": ("محتوا و کلمات کلیدی", 68)}),
        with_categories(overall_score=45, scores={"content": ("محتوا و کلمات کلیدی", 76)}),
    )
    row = by_metric(trend)["category:content"]

    assert row["direction"] == "better"
    assert row["label_fa"] == "محتوا و کلمات کلیدی"
    assert trend["better"] == ["category:content"]


def test_a_category_that_barely_moved_is_still_level():
    trend = trends.compare(
        with_categories(scores={"technical": ("فنی", 70)}),
        with_categories(scores={"technical": ("فنی", 72)}),
    )
    assert by_metric(trend)["category:technical"]["direction"] == "level"


def test_a_category_the_previous_run_never_had_is_not_compared():
    trend = trends.compare(
        with_categories(scores={"technical": ("فنی", 70)}),
        with_categories(scores={"technical": ("فنی", 70), "ai": ("جست‌وجوی هوش مصنوعی", 90)}),
    )
    assert "category:ai" not in by_metric(trend)


def test_the_biggest_movers_come_first():
    trend = trends.compare(
        with_categories(scores={"a": ("الف", 50), "b": ("ب", 50), "c": ("ج", 50)}),
        with_categories(scores={"a": ("الف", 52), "b": ("ب", 80), "c": ("ج", 40)}),
    )
    categories = [c["metric"] for c in trend["changes"] if c["metric"].startswith("category:")]
    assert categories == ["category:b", "category:c", "category:a"]


def test_categories_survive_a_crawl_of_a_different_size():
    # They are scores out of a hundred, not counts, so unlike total_issues
    # they mean the same thing on a sample half the size.
    trend = trends.compare(
        with_categories(pages=100, scores={"content": ("محتوا", 60)}),
        with_categories(pages=10, scores={"content": ("محتوا", 80)}),
    )
    assert trend["comparable_sample"] is False
    assert by_metric(trend)["category:content"]["direction"] == "better"


def test_a_category_is_named_in_persian_even_though_the_event_carries_no_label():
    # The crawl service trims its payload to the key and the score. Found in a
    # live run, where the trend table read "content" and "ai_search".
    trend = trends.compare(
        {"crawl": {"pages_crawled": 40, "category_scores": [{"category": "content", "score": 68}]},
         "headline": {}},
        {"crawl": {"pages_crawled": 40, "category_scores": [{"category": "content", "score": 76}]},
         "headline": {}},
    )
    assert by_metric(trend)["category:content"]["label_fa"] == "محتوا و کلمات کلیدی"


def test_a_category_the_engine_gains_later_is_shown_rather_than_dropped():
    trend = trends.compare(
        {"crawl": {"pages_crawled": 40, "category_scores": [{"category": "brand_new", "score": 10}]},
         "headline": {}},
        {"crawl": {"pages_crawled": 40, "category_scores": [{"category": "brand_new", "score": 40}]},
         "headline": {}},
    )
    assert by_metric(trend)["category:brand_new"]["label_fa"] == "brand_new"
