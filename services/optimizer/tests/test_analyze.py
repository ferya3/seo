"""What to rewrite, and what any rewrite has to satisfy.

The rule layer is a pure function of a crawl report and a keyword study, so
every judgement it makes is pinned here — including the one that matters most:
when it refuses to produce a candidate because producing one would mean making
something up.

    pytest services/optimizer
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.optimizer import analyze  # noqa: E402


def page(url, title="", description="", headings=(), words=500, indexable=True):
    return {
        "url": url, "title": title, "description": description,
        "headings": [{"level": lvl, "text": text} for lvl, text in headings],
        "words": words, "indexable": indexable,
    }


def research(*rows):
    return {"keywords": [{"keyword": k, "demand": d, "intent": "commercial"} for k, d in rows]}


KEYWORDS = research(("کفش ورزشی", 900), ("کفش دویدن", 400))
GOOD_DESCRIPTION = "خرید کفش ورزشی مردانه و زنانه با ارسال سریع و ضمانت اصالت کالا از فروشگاه ما"


# ------------------------------------------------------------------ the title


def test_a_long_title_is_trimmed_at_a_word_boundary():
    """A hard character cut leaves half a word on the results page."""
    title = "فروشگاه اینترنتی کفش ورزشی مردانه و زنانه با بهترین قیمت و ارسال رایگان"
    trimmed = analyze.trim_title(title)

    assert len(trimmed) <= analyze.TITLE_MAX
    assert title.startswith(trimmed)
    assert not trimmed.endswith(" ")


def test_the_brand_suffix_goes_before_the_words_do():
    """It is the part the reader can already see in the domain, so it is the
    first thing a person would drop."""
    title = "کفش ورزشی مردانه ساق بلند مناسب دویدن | فروشگاه ورزشی آنلاین ایران"
    assert analyze.trim_title(title) == "کفش ورزشی مردانه ساق بلند مناسب دویدن"


def test_a_short_enough_title_is_left_alone():
    assert analyze.trim_title("کفش ورزشی") == "کفش ورزشی"


def test_a_missing_title_is_built_from_the_pages_own_h1():
    p = analyze.build_pages({"pages": [
        page("https://site.test/x", headings=[(1, "کفش دویدن حرفه‌ای")])
    ]})[0]
    fix = analyze.title_fix(p, "کفش دویدن")

    assert fix["problem"] == "missing"
    assert fix["candidate"] == "کفش دویدن حرفه‌ای"


def test_a_missing_title_with_no_headings_falls_back_to_the_url():
    p = analyze.build_pages({"pages": [page("https://site.test/blog/کفش-دویدن")]})[0]
    assert "کفش" in (analyze.title_fix(p, None)["candidate"] or "")


def test_a_title_without_the_keyword_gets_no_candidate():
    """Inserting the keyword mechanically produces exactly the stuffed title
    this tool exists to argue against. The brief says what is missing and the
    writing is left to something that can write."""
    p = analyze.Page(url="x", title="بهترین انتخاب برای ورزشکاران حرفه‌ای")
    fix = analyze.title_fix(p, "کفش دویدن")

    assert fix["problem"] == "missing_keyword"
    assert fix["candidate"] is None
    assert fix["keyword"] == "کفش دویدن"


def test_a_title_that_is_already_right_produces_nothing():
    p = analyze.Page(url="x", title="خرید کفش ورزشی مردانه با ارسال سریع")
    assert analyze.title_fix(p, "کفش ورزشی") is None


def test_a_short_title_is_reported_as_room_left_unused():
    p = analyze.Page(url="x", title="کفش ورزشی")
    fix = analyze.title_fix(p, "کفش ورزشی")
    assert fix["problem"] == "too_short"


# ------------------------------------------------------------ the description


def test_a_missing_description_is_a_brief_not_a_guess():
    """There is nothing on the page to derive a snippet from without writing
    one, so the rule layer refuses and says what it must contain."""
    fix = analyze.description_fix(analyze.Page(url="x", title="کفش"), "کفش ورزشی")

    assert fix["problem"] == "missing"
    assert fix["candidate"] is None
    assert fix["min_length"] == analyze.DESC_MIN
    assert fix["max_length"] == analyze.DESC_MAX


def test_a_description_missing_the_keyword_is_flagged():
    p = analyze.Page(url="x", description="بهترین کالاها را از ما بخواهید" + " باکیفیت" * 8)
    assert analyze.description_fix(p, "کفش ورزشی")["problem"] == "missing_keyword"


def test_a_good_description_produces_nothing():
    p = analyze.Page(url="x", description=GOOD_DESCRIPTION)
    assert analyze.description_fix(p, "کفش ورزشی") is None


# -------------------------------------------------------------------- the h1


def test_a_missing_h1_can_borrow_the_title():
    p = analyze.Page(url="x", title="کفش دویدن حرفه‌ای")
    fix = analyze.h1_fix(p, "کفش دویدن")
    assert fix["problem"] == "missing"
    assert fix["candidate"] == "کفش دویدن حرفه‌ای"


def test_an_h1_that_covers_the_keyword_is_left_alone():
    p = analyze.Page(url="x", h1="کفش دویدن سبک")
    assert analyze.h1_fix(p, "کفش دویدن") is None


# --------------------------------------------------------------- the keyword


def test_a_page_is_matched_to_what_it_already_talks_about():
    """Not to the highest-demand keyword. Telling someone to rewrite their
    contact page around a commercial query is how a tool gets ignored."""
    p = analyze.Page(url="x", title="کفش دویدن مردانه")
    assert analyze.keyword_for(p, KEYWORDS["keywords"])["keyword"] == "کفش دویدن"


def test_a_page_about_nothing_in_the_study_gets_no_keyword():
    p = analyze.Page(url="x", title="تماس با ما", h1="تماس با ما")
    assert analyze.keyword_for(p, KEYWORDS["keywords"]) is None


def test_demand_only_breaks_a_tie():
    """Two keywords the page matches equally well: the one worth more wins,
    but a better match always beats a bigger number."""
    keywords = research(("کفش", 5000), ("کفش دویدن مردانه", 100))["keywords"]
    p = analyze.Page(url="x", title="کفش دویدن مردانه")
    assert analyze.keyword_for(p, keywords)["keyword"] == "کفش دویدن مردانه"


# ------------------------------------------------------------------ the plan


def test_a_page_with_nothing_wrong_is_not_in_the_plan():
    site = {"pages": [page(
        "https://site.test/",
        title="خرید کفش ورزشی مردانه با ارسال سریع",
        description=GOOD_DESCRIPTION,
        headings=[(1, "کفش ورزشی مردانه")],
    )]}
    assert analyze.summarise(site, KEYWORDS)["plans"] == []


def test_pages_are_ordered_by_what_is_worth_most():
    site = {"pages": [
        page("https://site.test/run", title="کفش دویدن"),          # demand 400
        page("https://site.test/sport", title="کفش ورزشی"),        # demand 900
    ]}
    plans = analyze.summarise(site, KEYWORDS)["plans"]
    assert [p["url"] for p in plans] == ["https://site.test/sport", "https://site.test/run"]


def test_the_plan_is_bounded():
    """A hundred rewrites is a list nobody starts."""
    site = {"pages": [page(f"https://site.test/{i}", title="کفش ورزشی") for i in range(30)]}
    result = analyze.summarise(site, KEYWORDS, limit=5)

    assert len(result["plans"]) == 5
    assert result["pages_with_fixes"] == 30       # the count is not truncated


def test_a_noindex_page_is_not_worth_rewriting():
    site = {"pages": [page("https://site.test/x", title="کفش ورزشی", indexable=False)]}
    assert analyze.summarise(site, KEYWORDS)["pages_examined"] == 0


def test_the_summary_counts_the_problems_by_kind():
    site = {"pages": [
        page("https://site.test/a", title="کفش ورزشی"),
        page("https://site.test/b", title="کفش دویدن"),
    ]}
    counts = analyze.summarise(site, KEYWORDS)["by_problem"]

    assert counts["too_short"] == 2          # both titles are under the floor
    assert counts["missing"] == 4            # description and h1 on both pages


def test_an_empty_crawl_is_survivable():
    result = analyze.summarise({}, KEYWORDS)
    assert result["pages_examined"] == 0
    assert result["plans"] == []


def test_a_site_with_no_keyword_study_still_gets_length_fixes():
    """Research returns nothing from some networks. The title length rules do
    not need a keyword, so they still apply."""
    site = {"pages": [page("https://site.test/x", title="ک")]}
    plans = analyze.summarise(site, {})["plans"]

    assert plans[0]["keyword"] is None
    assert any(fix["problem"] == "too_short" for fix in plans[0]["fixes"])
