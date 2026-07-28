"""Comparing your crawl with theirs.

The judgements pinned here are the ones that decide whether the report is
honest: that a capped crawl is never treated as a site's size, that a sample
too small to mean anything is named instead of averaged in, and that one
competitor's house style never reads as a gap in yours.

    pytest services/competitor
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.competitor import analyze  # noqa: E402


def page(url, title=None, description=None, headings=(), words=500, indexable=True):
    return {
        "url": url, "title": title, "description": description,
        "headings": [{"level": 1, "text": text} for text in headings],
        "words": words, "indexable": indexable,
    }


def site(host, count=6, **kwargs):
    """A site whose pages are all alike, so a test can vary one thing."""
    return {
        "start_url": f"https://{host}/",
        "pages": [page(f"https://{host}/{i}", **kwargs) for i in range(count)],
    }


GOOD_TITLE = "کفش ورزشی مردانه سبک و راحت"          # 27 chars, inside the band
LONG_TITLE = "کفش ورزشی مردانه سبک و راحت برای دویدن روزانه در پارک و خیابان شهر"


# --------------------------------------------------------------- the profile


def test_a_profile_is_rates_and_medians_never_a_page_count():
    # The whole comparison rests on this: crawls are capped, so a count of
    # pages measures the cap. Nothing in a profile may be one.
    report = site("mine.test", count=6, title=GOOD_TITLE, words=800)
    result = analyze.profile(analyze.build_site(report))

    assert result["titled"] == 100.0
    assert result["median_words"] == 800
    # `sampled` is there to be read, not compared — and it is the only count.
    assert result["sampled"] == 6
    medians = ("sampled", "median_words", "median_headings")
    counts = [
        key for key, value in result.items()
        if isinstance(value, int) and not isinstance(value, bool) and key not in medians
    ]
    assert counts == []


def test_noindex_pages_are_not_competing_and_are_left_out():
    report = {"start_url": "https://mine.test/", "pages": [
        page("https://mine.test/a", title=GOOD_TITLE),
        page("https://mine.test/b", title=GOOD_TITLE),
        page("https://mine.test/staging", title=None, indexable=False),
    ]}
    result = analyze.profile(analyze.build_site(report))

    assert result["sampled"] == 2
    assert result["titled"] == 100.0     # not 67 — the noindex page is not in the race


def test_title_in_range_is_not_the_same_as_having_a_title():
    report = site("mine.test", count=4, title=LONG_TITLE)
    result = analyze.profile(analyze.build_site(report))

    assert result["titled"] == 100.0
    assert result["title_in_range"] == 0.0


def test_the_label_is_the_host_a_person_would_name():
    assert analyze.site_label({"start_url": "https://www.Rival.test/shoes"}) == "rival.test"
    assert analyze.site_label({}) == "unknown"


# --------------------------------------------------------------- the verdicts


def test_a_small_difference_is_not_a_finding():
    # 62% against 58% is two samples of one practice. Calling that "behind"
    # fills the report with work that changes nothing.
    mine = {"titled": 62.0, "title_in_range": 50.0, "described": 50.0, "has_h1": 50.0,
            "thin": 50.0, "median_words": 500, "comparable": True}
    theirs = [{"titled": 58.0, "title_in_range": 50.0, "described": 50.0, "has_h1": 50.0,
               "thin": 50.0, "median_words": 500, "comparable": True, "label": "a.test"}]

    verdicts = {v["metric"]: v for v in analyze.rate_verdicts(mine, theirs)}
    assert verdicts["titled"]["verdict"] == "level"


def test_thin_pages_are_the_metric_where_less_is_better():
    mine = {"titled": 90.0, "title_in_range": 90.0, "described": 90.0, "has_h1": 90.0,
            "thin": 80.0, "median_words": 200, "comparable": True}
    theirs = [{"titled": 90.0, "title_in_range": 90.0, "described": 90.0, "has_h1": 90.0,
               "thin": 10.0, "median_words": 900, "comparable": True, "label": "a.test"}]

    verdicts = {v["metric"]: v for v in analyze.rate_verdicts(mine, theirs)}
    # More thin pages than they have is worse, however the arithmetic signs
    # happen to fall out.
    assert verdicts["thin"]["verdict"] == "behind"


def test_the_bar_is_the_median_competitor_not_the_best_one():
    mine = {"titled": 60.0, "title_in_range": 60.0, "described": 60.0, "has_h1": 60.0,
            "thin": 20.0, "median_words": 500, "comparable": True}
    theirs = [
        {"titled": 100.0, "title_in_range": 60.0, "described": 60.0, "has_h1": 60.0,
         "thin": 20.0, "median_words": 500, "comparable": True, "label": "a.test"},
        {"titled": 62.0, "title_in_range": 60.0, "described": 60.0, "has_h1": 60.0,
         "thin": 20.0, "median_words": 500, "comparable": True, "label": "b.test"},
        {"titled": 58.0, "title_in_range": 60.0, "described": 60.0, "has_h1": 60.0,
         "thin": 20.0, "median_words": 500, "comparable": True, "label": "c.test"},
    ]

    verdicts = {v["metric"]: v for v in analyze.rate_verdicts(mine, theirs)}
    # One site at 100% does not make 100% the standard.
    assert verdicts["titled"]["competitor_median"] == 62.0
    assert verdicts["titled"]["verdict"] == "level"


def test_a_sample_too_small_to_mean_anything_is_not_averaged_in():
    mine = {"titled": 90.0, "title_in_range": 90.0, "described": 90.0, "has_h1": 90.0,
            "thin": 10.0, "median_words": 800, "comparable": True}
    theirs = [{"titled": 0.0, "title_in_range": 0.0, "described": 0.0, "has_h1": 0.0,
               "thin": 100.0, "median_words": 10, "comparable": False, "label": "tiny.test"}]

    # Nothing comparable left, so there is no verdict to give — rather than a
    # verdict founded on three pages.
    assert analyze.rate_verdicts(mine, theirs) == []


def test_length_is_a_ratio_because_subtraction_cannot_tell_practices_apart():
    mine = {"median_words": 400, "comparable": True}
    close = [{"median_words": 500, "comparable": True}]
    far = [{"median_words": 1600, "comparable": True}]

    assert analyze.length_verdict(mine, close)["verdict"] == "level"
    behind = analyze.length_verdict(mine, far)
    assert behind["verdict"] == "behind"
    assert behind["ratio"] == 4.0


def test_no_length_verdict_when_there_is_nothing_to_compare():
    assert analyze.length_verdict({"median_words": 0, "comparable": True},
                                  [{"median_words": 900, "comparable": True}]) is None
    assert analyze.length_verdict({"median_words": 500, "comparable": True}, []) is None


# ----------------------------------------------------------------- the themes


def test_a_term_is_counted_once_per_page_not_once_per_mention():
    # A term in the site's header would otherwise outrank every real subject.
    report = {"start_url": "https://a.test/", "pages": [
        page("https://a.test/1", title="کفش کفش کفش", description="کفش", headings=["کفش"]),
    ]}
    assert analyze.terms(analyze.build_site(report))["کفش"] == 1


def test_one_competitor_using_a_word_is_not_a_theme():
    mine = analyze.build_site(site("mine.test", title="فروشگاه ورزشی مردانه"))
    lonely = analyze.build_site(site("a.test", title="اسکیت روی یخ حرفه‌ای"))
    other = analyze.build_site(site("b.test", title="فروشگاه ورزشی مردانه"))

    found = [row["term"] for row in analyze.themes(mine, [lonely, other])]
    assert "اسکیت" not in found


def test_a_term_two_competitors_use_and_you_do_not_is_a_theme():
    mine = analyze.build_site(site("mine.test", title="فروشگاه کفش"))
    a = analyze.build_site(site("a.test", title="کفش دویدن ماراتن"))
    b = analyze.build_site(site("b.test", title="کفش ماراتن سبک"))

    rows = analyze.themes(mine, [a, b])
    assert rows[0]["term"] == "ماراتن"
    assert rows[0]["competitors"] == 2


def test_persian_spelled_two_ways_is_one_word():
    # Arabic yeh on their side, Persian yeh on yours. Without folding this
    # reports a gap on a subject you already cover.
    mine = analyze.build_site(site("mine.test", title="کفش ورزشی مردانه"))
    a = analyze.build_site(site("a.test", title="کفش ورزشي مردانه"))
    b = analyze.build_site(site("b.test", title="کفش ورزشي مردانه"))

    assert [r["term"] for r in analyze.themes(mine, [a, b])] == []


def test_a_competitor_too_small_to_compare_does_not_contribute_themes():
    mine = analyze.build_site(site("mine.test", title="فروشگاه کفش"))
    a = analyze.build_site(site("a.test", count=2, title="کفش ماراتن"))
    b = analyze.build_site(site("b.test", count=2, title="کفش ماراتن"))

    assert analyze.themes(mine, [a, b]) == []


def test_shared_themes_are_the_ground_you_are_already_on():
    mine = analyze.build_site(site("mine.test", title="کفش دویدن مردانه"))
    a = analyze.build_site(site("a.test", title="کفش دویدن زنانه"))
    b = analyze.build_site(site("b.test", title="کفش دویدن بچگانه"))

    shared = {row["term"] for row in analyze.shared_themes(mine, [a, b])}
    assert "دویدن" in shared and "کفش" in shared


# ----------------------------------------------------------------- the report


def test_a_comparison_needs_someone_to_compare_with():
    with pytest.raises(analyze.NoComparison):
        analyze.summarise(site("mine.test"), [])


def test_the_report_names_who_was_left_out_rather_than_dropping_them():
    report = analyze.summarise(
        site("mine.test", count=8, title=GOOD_TITLE, words=900),
        [site("big.test", count=8, title=GOOD_TITLE, words=900),
         site("tiny.test", count=2, title=None, words=50)],
    )

    assert report["ignored"] == ["tiny.test"]
    assert report["compared_against"] == 1
    # Still listed, so nobody wonders where the third site went.
    assert {p["label"] for p in report["competitors"]} == {"big.test", "tiny.test"}


def test_the_report_says_what_it_did_not_compare():
    report = analyze.summarise(site("mine.test"), [site("a.test")])
    assert "تعداد صفحه" in report["method"]


def test_being_ahead_is_reported_as_well_as_being_behind():
    report = analyze.summarise(
        site("mine.test", count=8, title=GOOD_TITLE, description="توضیح کامل صفحه", words=1200),
        [site("weak.test", count=8, title=None, description=None, words=100)],
    )

    assert "titled" in report["ahead_on"]
    assert report["behind_on"] == []
    assert report["content_length"]["verdict"] == "ahead"


def test_a_site_with_no_pages_at_all_does_not_crash_the_comparison():
    report = analyze.summarise(
        site("mine.test", count=6, title=GOOD_TITLE),
        [{"start_url": "https://empty.test/", "pages": []}],
    )
    assert report["ignored"] == ["empty.test"]
    assert report["verdicts"] == []
