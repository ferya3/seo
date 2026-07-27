"""Coverage and cannibalisation: a crawl and a keyword study, held together.

Pure functions of two dictionaries, so every judgement is pinned here — most
importantly the Persian text folding, which is what decides whether a page and
a keyword are talking about the same thing at all.

    pytest services/content
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.content import analyze  # noqa: E402


def page(url, title=None, description=None, headings=(), words=500, indexable=True):
    return {
        "url": url, "title": title, "description": description,
        "headings": [{"level": 2, "text": text} for text in headings],
        "words": words, "indexable": indexable,
    }


def research(*keywords):
    return {"keywords": [
        {"keyword": k, "demand": d, "intent": i} for k, d, i in keywords
    ]}


SITE = {"pages": [
    page("https://site.test/", "فروشگاه کفش ورزشی", "خرید انواع کفش ورزشی"),
    page("https://site.test/running", "کفش دویدن حرفه‌ای", headings=["کفش دویدن سبک"]),
    page("https://site.test/about", "درباره ما", "تیم ما"),
]}

KEYWORDS = research(
    ("کفش ورزشی", 900, "commercial"),
    ("کفش دویدن", 400, "commercial"),
    ("کفش کوهنوردی", 700, "commercial"),
)


# ------------------------------------------------------------------ the words


def test_arabic_and_persian_spellings_are_the_same_word():
    """A page titled with Arabic yeh would never match a keyword written with
    Persian yeh, and the gap report would invent work already done."""
    assert analyze.normalise("کفش ورزشي") == analyze.normalise("کفش ورزشی")
    assert analyze.normalise("كفش") == analyze.normalise("کفش")


def test_a_zero_width_non_joiner_is_a_word_boundary():
    # "کفش‌های" is two words for matching, not one unrecognisable token.
    assert analyze.words("کفش‌های ورزشی") == ["کفش", "ورزشی"]


def test_diacritics_and_tatweel_are_ignored():
    assert analyze.normalise("کِفـش") == "کفش"


def test_common_words_do_not_carry_intent():
    assert analyze.words("کفش برای دویدن در پاییز") == ["کفش", "دویدن", "پاییز"]


def test_case_and_punctuation_fold_away():
    assert analyze.normalise("Running-Shoes!") == "running shoes"


# ---------------------------------------------------------------- the matching


def test_a_page_targets_the_keyword_in_its_title():
    p = analyze.build_pages(SITE)[0]
    assert analyze.targets(p, "کفش ورزشی") is True


def test_a_keyword_only_half_present_does_not_match():
    """Otherwise every page mentioning "کفش" would claim every shoe keyword and
    the gap list would come back empty and useless."""
    p = analyze.build_pages(SITE)[0]
    assert analyze.targets(p, "کفش کوهنوردی زمستانی") is False


def test_a_longer_page_title_still_matches_the_shorter_keyword():
    p = analyze.Page(url="x", title="کفش ورزشی مردانه ساق بلند")
    assert analyze.targets(p, "کفش ورزشی") is True


def test_headings_count_as_a_declaration():
    p = analyze.Page(url="x", title="خانه", headings=["کفش پیاده‌روی روزمره"])
    assert analyze.targets(p, "کفش پیاده‌روی") is True


def test_an_empty_keyword_matches_nothing():
    assert analyze.targets(analyze.Page(url="x", title="کفش"), "") is False
    assert analyze.targets(analyze.Page(url="x", title="کفش"), "و در به") is False


# ---------------------------------------------------------------- the findings


def test_a_keyword_with_no_page_is_a_gap():
    result = analyze.summarise(SITE, KEYWORDS)
    assert [row["keyword"] for row in result["gaps"]] == ["کفش کوهنوردی"]
    assert result["gaps"][0]["demand"] == 700


def test_gaps_are_ordered_by_demand():
    """The biggest missing page first, not the alphabetically luckiest."""
    result = analyze.summarise(
        {"pages": []},
        research(("الف", 10, "info"), ("ب", 900, "commercial"), ("پ", 300, "info")),
    )
    assert [row["keyword"] for row in result["gaps"]] == ["ب", "پ", "الف"]


def test_two_pages_on_one_keyword_are_reported_as_competing():
    """Invisible to a per-page audit: both pages look fine on their own."""
    site = {"pages": [
        page("https://site.test/a", "کفش دویدن مردانه"),
        page("https://site.test/b", "بهترین کفش دویدن", words=1200),
    ]}
    result = analyze.summarise(site, research(("کفش دویدن", 400, "commercial")))

    clash = result["cannibalisation"][0]
    assert clash["keyword"] == "کفش دویدن"
    assert clash["count"] == 2
    # Longest page first: that is the one to keep and point the others at.
    assert clash["pages"][0]["url"] == "https://site.test/b"


def test_one_page_on_one_keyword_is_not_a_clash():
    assert analyze.summarise(SITE, KEYWORDS)["cannibalisation"] == []


def test_a_noindex_page_neither_covers_nor_competes():
    """A page nobody is allowed to index cannot be the page that covers a
    keyword, and reporting it as competition would send someone rewriting a
    page that never had a chance."""
    site = {"pages": [page("https://site.test/x", "کفش کوهنوردی", indexable=False)]}
    result = analyze.summarise(site, research(("کفش کوهنوردی", 700, "commercial")))

    assert result["covered"] == 0
    assert result["gaps"][0]["keyword"] == "کفش کوهنوردی"


def test_a_page_matching_no_keyword_is_listed_not_condemned():
    """An About page should not target a commercial query. This is a list to
    look at, not a count of errors."""
    result = analyze.summarise(SITE, KEYWORDS)
    assert [row["url"] for row in result["untargeted_pages"]] == ["https://site.test/about"]


def test_coverage_is_a_percentage_of_the_keywords_studied():
    result = analyze.summarise(SITE, KEYWORDS)
    assert result["keywords"] == 3
    assert result["covered"] == 2
    assert result["coverage"] == 66.7


# ------------------------------------------------------------------ the shapes


def test_the_event_preview_and_the_full_report_are_both_readable():
    """The research service names the rows `keywords`; its event names them
    `top_keywords`. A consumer handling only one would analyse nothing and say
    everything was covered."""
    rows = [{"keyword": "کفش ورزشی", "demand": 10, "intent": "commercial"}]
    assert analyze.build_keywords({"keywords": rows}) == analyze.build_keywords(
        {"top_keywords": rows}
    )


def test_a_bare_string_keyword_still_works():
    assert analyze.build_keywords({"keywords": ["کفش"]})[0]["keyword"] == "کفش"


def test_no_keywords_is_zero_coverage_not_a_crash():
    """Research finding nothing is a real answer about a site — the same case
    that makes the workflow skip the rank check."""
    result = analyze.summarise(SITE, {})
    assert result["coverage"] == 0.0
    assert result["gaps"] == []


def test_an_empty_crawl_is_survivable():
    result = analyze.summarise({}, KEYWORDS)
    assert result["pages"] == 0
    assert len(result["gaps"]) == 3


def test_the_report_says_how_it_matched():
    """A page can cover a topic in its body text and still be counted as a gap.
    That limit is stated in the report rather than left to be discovered."""
    assert "متن بدنه" in analyze.summarise(SITE, KEYWORDS)["method"]
