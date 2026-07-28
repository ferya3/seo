"""What changed since the last time this site was audited.

Scheduling made the tool run itself; this is what makes the runs a series
rather than a pile. A weekly report that repeats the same absolute numbers
answers "how is the site", which the reader already knows. The question they
actually have by the second run is "is it getting better", and nothing in a
single report can answer it.

The comparison is arithmetic over two reports this system already stored, so
unlike most of what is left it needs no network, no key and no third party.

Four things are wrong in the obvious implementation, and each one is a rule
here:

  * **Direction is not sign.** A score going up is good and issues going up is
    bad. A diff that only reports "+3" leaves the reader to remember which
    metrics are which, and they will not.
  * **Missing is not zero.** If the rank check was skipped last week and ran
    this week, "keywords ranked: 0 → 5" is a lie about the site — the truth is
    that the step ran this time. Either side missing means no comparison for
    that metric, and the metric says so.
  * **Small moves are not news.** A score of 71 against 72 is the same site.
    Reporting it trains people to ignore the section.
  * **A different crawl is a different sample.** Counts scale with how many
    pages were fetched, so a run capped at 10 pages and one capped at 100 are
    not comparable on anything that counts things. The size change is carried
    into the report rather than silently invalidating the numbers.

Pure functions of two dictionaries: no network, no database, no clock.
"""

from __future__ import annotations

from typing import Any

# Which headline numbers are worth tracking, which way is better, and how much
# has to move before it counts. The threshold is in the metric's own units.
#
# `pages_to_rewrite` is deliberately absent: it is a function of how many pages
# the plan was asked to cover, so it moves when a setting changes rather than
# when the site does.
METRICS = (
    ("overall_score",    "higher", 2,   "امتیاز کلی"),
    ("total_issues",     "lower",  2,   "تعداد ایرادها"),
    ("keywords_found",   "higher", 5,   "کلمات کلیدی"),
    ("keywords_ranked",  "higher", 1,   "کلمه‌ی رتبه‌گرفته"),
    ("average_position", "lower",  0.5, "میانگین جایگاه"),
    ("orphan_pages",     "lower",  1,   "صفحه‌ی یتیم"),
    ("keyword_coverage", "higher", 5,   "پوشش کلمات کلیدی"),
    ("behind_on",        "lower",  1,   "سنجه‌ای که از رقبا عقب‌اید"),
)

# How much the crawl size may differ before the counting metrics stop being
# comparable. Twenty percent covers an ordinary week of a site growing; double
# is a different crawl, not a different site.
SAMPLE_TOLERANCE = 0.2

# Category scores are out of a hundred like the overall score, but they move in
# smaller steps because each covers fewer rules, so the bar for "this moved" is
# lower than the two points the overall score needs.
CATEGORY_THRESHOLD = 3

# The event carries a category key and a score, not a label — the crawl service
# trims its payload to what a consumer cannot look up, and this is a consumer
# that can. Copied from the engine rather than imported: the orchestrator does
# not depend on the engine, and it is not going to start over eight strings.
# An unknown key falls through to itself rather than being dropped.
CATEGORY_FA = {
    "indexing": "ایندکس‌شدن و خزش",
    "content": "محتوا و کلمات کلیدی",
    "technical": "فنی",
    "performance": "سرعت و Core Web Vitals",
    "structured_data": "داده ساختاریافته",
    "links": "لینک‌سازی",
    "images": "تصاویر",
    "eeat": "اعتبار و E-E-A-T",
    "international": "چندزبانه",
    "ai_search": "جستجوی هوش مصنوعی",
}

# The metrics that scale with how many pages were crawled. Compared only when
# the two crawls were close enough in size — see SAMPLE_TOLERANCE.
COUNTING = frozenset({"total_issues", "orphan_pages"})


def compare(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any] | None:
    """The change between two workflow reports, or None if there is nothing to
    compare against.

    None rather than an empty diff: "this is the first run" and "nothing
    changed" are different answers, and a section of zeros would read as the
    second when it means the first.
    """
    if not previous:
        return None

    before = previous.get("headline") or {}
    after = current.get("headline") or {}
    pages_before = _pages(previous)
    pages_after = _pages(current)
    comparable_sample = _similar(pages_before, pages_after)

    changes = []
    for key, direction, threshold, label_fa in METRICS:
        old, new = before.get(key), after.get(key)
        if old is None or new is None:
            # Not "unchanged" and not zero: one of the two runs never measured
            # this. Saying nothing is the only honest option.
            continue
        if key in COUNTING and not comparable_sample:
            continue

        delta = round(_number(new) - _number(old), 2)
        moved = abs(delta) >= threshold
        better = delta > 0 if direction == "higher" else delta < 0

        changes.append({
            "metric": key,
            "label_fa": label_fa,
            "before": old,
            "after": new,
            "change": delta,
            "direction": "level" if not moved else ("better" if better else "worse"),
        })

    changes += _categories(previous, current)

    return {
        "compared_with": previous.get("workflow_id"),
        "compared_at": previous.get("finished_at"),
        "changes": changes,
        "better": [c["metric"] for c in changes if c["direction"] == "better"],
        "worse": [c["metric"] for c in changes if c["direction"] == "worse"],
        "pages_before": pages_before,
        "pages_after": pages_after,
        # Said rather than assumed: when the two crawls are different sizes,
        # the metrics that count things were left out, and a reader who is not
        # told will read their absence as "no change".
        "comparable_sample": comparable_sample,
        "note": None if comparable_sample else (
            f"این بار {pages_after} صفحه خزیده شد و دفعه‌ی قبل {pages_before} — "
            "سنجه‌هایی که چیزی را می‌شمارند مقایسه نشدند، چون با اندازه‌ی خزش "
            "بالا و پایین می‌روند نه با وضعیت سایت."
        ),
    }


def headline(trend: dict[str, Any] | None) -> str | None:
    """One sentence, for the top of a report or a notification.

    Written here rather than in the renderer because it is a judgement — which
    way the site is going — and the answer has to be the same everywhere it
    appears.
    """
    if not trend or not trend.get("changes"):
        return None

    better, worse = len(trend["better"]), len(trend["worse"])
    if not better and not worse:
        return "نسبت به اجرای قبلی تغییر معناداری نیست."

    score = next((c for c in trend["changes"] if c["metric"] == "overall_score"), None)
    lead = ""
    if score and score["direction"] != "level":
        way = "بالا رفته" if score["change"] > 0 else "پایین آمده"
        lead = f"امتیاز کلی از {score['before']} به {score['after']} {way}. "

    if better and not worse:
        return lead + f"{better} سنجه بهتر شده و هیچ‌کدام بدتر نشده."
    if worse and not better:
        return lead + f"{worse} سنجه بدتر شده و هیچ‌کدام بهتر نشده."
    return lead + f"{better} سنجه بهتر و {worse} سنجه بدتر شده."


def _categories(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """The per-category scores, which are where ordinary work shows up first.

    Found in a live run: a site that gained a meta description on every page
    and tripled its word count reported "no meaningful change", because the
    overall score and the issue count both happened to land where they were.
    Its content score had gone from 68 to 76 and nothing said so.

    Higher is better for all of them, and they are scores out of a hundred
    rather than counts, so they survive a crawl of a different size.
    """
    before = {c.get("category"): c for c in (_crawl(previous).get("category_scores") or [])}
    after = {c.get("category"): c for c in (_crawl(current).get("category_scores") or [])}

    rows = []
    for key, now in after.items():
        was = before.get(key)
        if was is None or now.get("score") is None or was.get("score") is None:
            continue

        delta = round(_number(now["score"]) - _number(was["score"]), 2)
        moved = abs(delta) >= CATEGORY_THRESHOLD
        rows.append({
            "metric": f"category:{key}",
            "label_fa": now.get("label_fa") or CATEGORY_FA.get(key, key),
            "before": was["score"],
            "after": now["score"],
            "change": delta,
            "direction": "level" if not moved else ("better" if delta > 0 else "worse"),
        })

    # Biggest movers first: a reader scanning eight category rows wants the two
    # that changed, not alphabetical order.
    rows.sort(key=lambda row: (-abs(row["change"]), row["label_fa"]))
    return rows


def _crawl(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("crawl") or {}


def _pages(report: dict[str, Any]) -> int:
    return int((report.get("crawl") or {}).get("pages_crawled") or 0)


def _similar(before: int, after: int) -> bool:
    """Whether two crawls fetched close enough to the same amount.

    Two runs that both crawled nothing are not comparable either — there is no
    sample, so there is nothing to say about counts.
    """
    if not before or not after:
        return False
    return abs(after - before) <= SAMPLE_TOLERANCE * max(before, after)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
