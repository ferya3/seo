"""The document a user can send to someone else.

Pure functions of a workflow report, so everything is pinned here — including
the two things that decide whether a report survives contact with a reader:
that it carries no external requests, and that a page title taken off someone
else's website cannot run script in the reader's browser.

    pytest services/reporting
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.reporting import render  # noqa: E402

WORKFLOW = {
    "workflow_id": "11111111-2222-3333-4444-555555555555",
    "goal": "site_audit",
    "status": "completed",
    "error": None,
    "created_at": "2026-07-27T09:00:00+00:00",
    "inputs": {"start_url": "https://site.test/"},
    "steps": [
        {"position": 1, "kind": "crawl", "status": "completed", "error": None},
        {"position": 2, "kind": "keyword_research", "status": "completed", "error": None},
        {"position": 3, "kind": "serp_check", "status": "skipped",
         "error": "research produced no keywords to rank-check"},
        {"position": 4, "kind": "link_analysis", "status": "completed", "error": None},
        {"position": 5, "kind": "content_analysis", "status": "completed", "error": None},
        {"position": 6, "kind": "optimizer_plan", "status": "completed", "error": None},
    ],
    "report": {
        "headline": {
            "overall_score": 87, "total_issues": 18, "keywords_found": 120,
            "keywords_ranked": None, "average_position": None, "orphan_pages": 0,
            "keyword_coverage": 60.0, "pages_to_rewrite": 3,
        },
        "summary": {
            "source": "rules",
            "text_fa": "امتیاز کلی سئوی سایت ۸۷ از ۱۰۰ است.",
            "next_actions": [
                {"action": "صفحه‌ای برای «کفش کوهنوردی» بساز.", "why": "هیچ صفحه‌ای ندارد.",
                 "effort": "زیاد", "impact": "زیاد"},
            ],
            "watch_outs": ["حجم جستجوی واقعی در این گزارش نیست."],
        },
        "crawl": {"start_url": "https://site.test/", "grade": "خوب"},
        "rankings": {
            "opportunities": [
                {"keyword": "کفش ورزشی", "position": None, "opportunity": 100},
                {"keyword": "کفش دویدن", "position": 3, "opportunity": 55},
            ],
            "top_competitors": [{"domain": "rival.com", "outranks_on": 4}],
        },
        "content": {"top_gaps": [{"keyword": "کفش کوهنوردی", "demand": 700}]},
        "links": {"top_opportunities": [
            {"url": "https://site.test/deep", "inlinks": 1, "authority": 12.4},
        ]},
        "optimizer": {"written_by": "rules", "pages": [
            {"url": "https://site.test/run", "fixes": 2},
        ]},
    },
}


# ------------------------------------------------------------------ markdown


def test_the_markdown_names_the_site_and_the_workflow():
    text = render.markdown(WORKFLOW)
    assert text.startswith("# گزارش سئو — https://site.test/")
    assert WORKFLOW["workflow_id"] in text


def test_the_summary_comes_before_the_evidence():
    text = render.markdown(WORKFLOW)
    assert text.index("## خلاصه") < text.index("## اعداد اصلی")
    assert text.index("## اعداد اصلی") < text.index("## مراحل")


def test_every_section_with_data_is_present():
    text = render.markdown(WORKFLOW)
    for heading in ["فرصت‌های رتبه", "رقبا", "صفحه‌هایی که هنوز نوشته نشده‌اند",
                    "صفحاتی که لینک داخلی کم دارند", "پیشنهاد بازنویسی"]:
        assert heading in text


def test_a_section_with_no_data_is_left_out_entirely():
    """An empty table invites the question "why is this blank"; the steps
    table already answers it."""
    thin = {**WORKFLOW, "report": {"headline": {"overall_score": 50}}}
    text = render.markdown(thin)
    assert "فرصت‌های رتبه" not in text
    assert "رقبا" not in text


def test_a_metric_that_was_never_measured_is_not_shown_as_a_dash():
    text = render.markdown(WORKFLOW)
    assert "میانگین جایگاه" not in text      # the rank check was skipped
    assert "پوشش کلمات (٪) | 60.0" in text


def test_a_skipped_step_keeps_its_reason():
    assert "research produced no keywords to rank-check" in render.markdown(WORKFLOW)


def test_an_unranked_keyword_is_never_printed_as_a_number():
    text = render.markdown(WORKFLOW)
    assert "| پیدا نشد |" in text


def test_a_failed_workflow_says_so_at_the_top():
    failed = {**WORKFLOW, "status": "failed", "error": "crawl timed out"}
    text = render.markdown(failed)
    assert "crawl timed out" in text.split("## ")[0]


def test_the_summary_says_who_wrote_it():
    assert "خلاصه (قانون‌محور)" in render.markdown(WORKFLOW)

    ai = {**WORKFLOW, "report": {**WORKFLOW["report"],
                                 "summary": {**WORKFLOW["report"]["summary"], "source": "ai"}}}
    assert "خلاصه (نوشته‌ی مدل)" in render.markdown(ai)


def test_an_empty_workflow_still_renders():
    text = render.markdown({})
    assert text.startswith("# گزارش سئو")
    assert "ایجنت سئو" in text


# ---------------------------------------------------------------------- html


def test_the_document_is_one_self_contained_file():
    """A report that pulls a stylesheet from a CDN looks broken when it is
    opened offline six months later — which is how a report gets read."""
    page = render.document(WORKFLOW)

    assert page.startswith("<!doctype html>")
    assert "<style>" in page
    for external in ["<link", "<script", "src=", "http://fonts", "cdn"]:
        assert external not in page


def test_the_document_is_right_to_left_and_persian():
    page = render.document(WORKFLOW)
    assert "lang='fa'" in page and "dir='rtl'" in page


def test_a_page_title_cannot_run_script_in_the_readers_browser():
    """Titles come off someone else's website. Rendering one raw is how a
    crawled page attacks whoever opens the report."""
    hostile = {**WORKFLOW, "inputs": {"start_url": "<script>alert(1)</script>"}}
    page = render.document(hostile)

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_hostile_text_inside_a_table_is_escaped_too():
    hostile = {**WORKFLOW, "report": {**WORKFLOW["report"], "rankings": {
        "opportunities": [{"keyword": "<img src=x onerror=alert(1)>",
                           "position": 1, "opportunity": 10}],
    }}}
    page = render.document(hostile)
    assert "onerror=alert(1)>" not in page
    assert "&lt;img" in page


def test_the_document_carries_the_same_sections_as_the_markdown():
    page = render.document(WORKFLOW)
    for heading in ["خلاصه", "اعداد اصلی", "مراحل", "فرصت‌های رتبه", "رقبا",
                    "پیشنهاد بازنویسی"]:
        assert heading in page


def test_printing_is_a_first_class_layout():
    """Printing is how this becomes a PDF, so the rules are in the file rather
    than left to the browser's defaults."""
    page = render.document(WORKFLOW)
    assert "@page" in page and "A4" in page
    assert "@media print" in page
    assert "break-inside" in page


def test_an_empty_workflow_still_produces_a_valid_page():
    page = render.document({})
    assert page.startswith("<!doctype html>")
    assert page.rstrip().endswith("</html>")


# --------------------------------------------------------------- competitors


def with_rivals() -> dict:
    """A workflow that also compared itself with two competitors."""
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["steps"] += [
        {"position": 7, "kind": "competitor_crawl", "status": "completed", "error": None},
        {"position": 8, "kind": "competitor_crawl", "status": "failed",
         "error": "connection refused"},
        {"position": 9, "kind": "competitor_check", "status": "completed", "error": None},
    ]
    workflow["report"]["headline"]["competitors_compared"] = 1
    workflow["report"]["headline"]["behind_on"] = 2
    workflow["report"]["competitors"] = {
        "compared_against": 1,
        "behind_on": ["described", "thin"],
        "top_missing_themes": ["ماراتن", "راهنمای خرید"],
    }
    return workflow


def test_the_missing_themes_reach_both_formats():
    text = render.markdown(with_rivals())
    html = render.document(with_rivals())

    assert "ماراتن" in text and "ماراتن" in html
    assert "رقیب مقایسه‌شده" in text and "رقیب مقایسه‌شده" in html


def test_the_document_says_how_many_competitors_the_verdict_rests_on():
    # One rival compared is a much weaker statement than four, and the reader
    # cannot judge the section without knowing which.
    html = render.document(with_rivals())
    assert "1 رقیب مقایسه شد" in html


def test_a_competitor_crawl_that_failed_is_named_in_persian_not_by_its_kind():
    html = render.document(with_rivals())
    assert "خزش سایت رقیب" in html
    assert "competitor_crawl" not in html


def test_a_report_without_competitors_has_no_competitor_section():
    html = render.document(WORKFLOW)
    assert "رقبا پوشش می‌دهند" not in html
    assert "رقیب مقایسه‌شده" not in html


# --------------------------------------------------------------------- trend


def with_trend() -> dict:
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["report"]["trend"] = {
        "compared_with": "aaaa-bbbb",
        "changes": [
            {"metric": "overall_score", "label_fa": "امتیاز کلی", "before": 70,
             "after": 87, "change": 17, "direction": "better"},
            {"metric": "total_issues", "label_fa": "تعداد ایرادها", "before": 9,
             "after": 18, "change": 9, "direction": "worse"},
            {"metric": "orphan_pages", "label_fa": "صفحه‌ی یتیم", "before": 0,
             "after": 0, "change": 0, "direction": "level"},
        ],
        "better": ["overall_score"], "worse": ["total_issues"],
        "comparable_sample": True, "note": None,
    }
    return workflow


def test_the_trend_says_which_way_is_better_rather_than_just_the_sign():
    # "+9" on issues is worse. An arrow alone makes the reader work that out.
    html = render.document(with_trend())
    text = render.markdown(with_trend())

    for out in (html, text):
        assert "+17 (بهتر)" in out
        assert "+9 (بدتر)" in out
        assert "0 (بدون تغییر معنادار)" in out


def test_the_note_about_an_incomparable_crawl_reaches_the_document():
    workflow = with_trend()
    workflow["report"]["trend"]["comparable_sample"] = False
    workflow["report"]["trend"]["note"] = "این بار 10 صفحه خزیده شد و دفعه‌ی قبل 100"

    assert "دفعه‌ی قبل 100" in render.document(workflow)
    assert "دفعه‌ی قبل 100" in render.markdown(workflow)


def test_a_first_run_has_no_trend_section():
    html = render.document(WORKFLOW)
    assert "نسبت به اجرای قبلی" not in html
