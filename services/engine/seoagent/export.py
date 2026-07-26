"""Markdown export for audit and keyword reports."""

from __future__ import annotations

from typing import Any

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
SEVERITY_ICON = {
    "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪",
}


def audit_markdown(report: dict[str, Any]) -> str:
    stats = report["stats"]
    out: list[str] = [
        f"# گزارش سئو — {report['start_url']}",
        "",
        f"**تاریخ:** {report['generated_at']}  ",
        f"**امتیاز کلی:** {report['overall_score']}/100 ({report['grade']})",
        "",
        "## آمار کلی",
        "",
        "| معیار | مقدار |",
        "| --- | --- |",
        f"| صفحات خزیده‌شده | {stats['pages_crawled']} |",
        f"| صفحات قابل ایندکس | {stats['indexable_pages']} |",
        f"| خطاها | {stats['errors']} |",
        f"| ریدایرکت‌ها | {stats['redirects']} |",
        f"| میانگین کلمات هر صفحه | {stats['avg_words']} |",
        f"| میانگین زمان پاسخ | {stats['avg_load_ms']} میلی‌ثانیه |",
        f"| آدرس‌های داخل سایت‌مپ | {stats['sitemap_urls']} |",
        f"| کل ایرادها | {stats['total_issues']} |",
        "",
        "## امتیاز هر بخش",
        "",
        "| بخش | امتیاز | تعداد ایراد |",
        "| --- | --- | --- |",
    ]
    for cat in report["category_scores"]:
        out.append(f"| {cat['label_fa']} ({cat['label_en']}) | {cat['score']}/100 | {cat['issue_count']} |")

    quick = report.get("quick_wins") or []
    if quick:
        out += ["", "## از کجا شروع کنم", ""]
        for index, issue in enumerate(quick, 1):
            out.append(f"{index}. **{issue['title_fa']}** — {issue['fix_fa']}")

    ai = report.get("ai_suggestions")
    if ai and not ai.get("error"):
        out += ["", "## تحلیل هوش مصنوعی", "", ai.get("summary", ""), ""]
        for priority in ai.get("priorities", []):
            out.append(
                f"- **{priority['action']}** (تلاش: {priority['effort']}، اثر: {priority['impact']})  \n"
                f"  {priority['why']}"
            )
        rewrites = ai.get("title_rewrites") or []
        if rewrites:
            out += ["", "### پیشنهاد بازنویسی عنوان‌ها", ""]
            for rewrite in rewrites:
                out += [
                    f"**{rewrite['url']}**",
                    f"- فعلی: {rewrite['current']}",
                    f"- پیشنهادی: {rewrite['suggested']}",
                    f"- متا دیسکریپشن: {rewrite['meta_description']}",
                    "",
                ]
        gaps = ai.get("content_gaps") or []
        if gaps:
            out += ["### شکاف‌های محتوایی", ""] + [f"- {g}" for g in gaps] + [""]

    out += ["", "## فهرست کامل ایرادها", ""]
    by_severity: dict[str, list[dict]] = {s: [] for s in SEVERITY_ORDER}
    for issue in report["issues"]:
        by_severity.setdefault(issue["severity"], []).append(issue)

    for severity in SEVERITY_ORDER:
        items = by_severity.get(severity) or []
        if not items:
            continue
        out += ["", f"### {SEVERITY_ICON[severity]} {items[0]['severity_fa']}", ""]
        for issue in items:
            out += [
                f"#### {issue['title_fa']} — `{issue['title_en']}`",
                "",
                f"*دسته:* {issue['category_fa']} | *صفحات متأثر:* {issue['affected_count']}",
                "",
                issue["detail_fa"],
                "",
                f"**راه‌حل:** {issue['fix_fa']}",
                "",
            ]
            if issue.get("docs"):
                out.append(f"[مستندات گوگل]({issue['docs']})")
                out.append("")
            if issue["urls"]:
                out.append("<details><summary>آدرس‌ها</summary>")
                out.append("")
                out += [f"- {u}" for u in issue["urls"][:25]]
                if issue["affected_count"] > 25:
                    out.append(f"- … و {issue['affected_count'] - 25} مورد دیگر")
                out += ["", "</details>", ""]

    notes = report.get("notes") or []
    if notes:
        out += ["", "## یادداشت‌ها", ""] + [f"- {n}" for n in notes]

    return "\n".join(out)


def keywords_markdown(report: dict[str, Any]) -> str:
    out: list[str] = [
        f"# تحقیق کلمات کلیدی — «{report['seed']}»",
        "",
        f"**زبان/کشور:** {report['lang']} / {report['country']}  ",
        f"**تعداد کلمات یافت‌شده:** {report['total']} (از {report['queries_sent']} پرس‌وجو)",
        "",
    ]

    ai = report.get("ai_suggestions")
    if ai and not ai.get("error"):
        out += ["## تحلیل هوش مصنوعی", "", ai.get("summary", ""), ""]
        for angle in ai.get("angles", []):
            out += [
                f"### {angle['title']}",
                "",
                f"*کلمه کلیدی هدف:* {angle['target_keyword']} | *نیت:* {angle['intent']}",
                "",
            ]
            out += [f"- {h}" for h in angle.get("outline", [])]
            out.append("")
        questions = ai.get("questions_to_answer") or []
        if questions:
            out += ["### سؤال‌هایی که باید جواب بدهی", ""] + [f"- {q}" for q in questions] + [""]
        entities = ai.get("entities") or []
        if entities:
            out += ["### موجودیت‌ها و اصطلاحات لازم", "", "، ".join(entities), ""]

    if report.get("trending"):
        out += ["## ترندهای امروز گوگل", "", "، ".join(report["trending"]), ""]

    out += [
        "## برنامه محتوایی پیشنهادی",
        "",
        "| کلمه کلیدی اصلی | نوع صفحه | نیت | اندازه خوشه | عنوان پیشنهادی |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report.get("plan", []):
        out.append(
            f"| {item['primary_keyword']} | {item['page_type']} | {item['intent_fa']} "
            f"| {item['cluster_size']} | {item['suggested_title']} |"
        )

    out += [
        "",
        "## کلمات کلیدی",
        "",
        "> «تقاضا» و «فرصت» امتیاز نسبی و تخمینی هستند که از داده‌ی پیشنهاد خودکار موتورهای "
        "جستجو محاسبه می‌شوند — حجم جستجوی واقعی نیستند.",
        "",
        "| کلمه کلیدی | تقاضا | فرصت | نیت | منابع |",
        "| --- | --- | --- | --- | --- |",
    ]
    for keyword in report.get("keywords", []):
        out.append(
            f"| {keyword['keyword']} | {keyword['demand']} | {keyword['opportunity']} "
            f"| {keyword['intent_fa']} | {'، '.join(keyword['sources'])} |"
        )

    if report.get("questions"):
        out += ["", "## پرسش‌های کاربران", ""] + [f"- {q}" for q in report["questions"]]

    if report.get("entities"):
        out += ["", "## موجودیت‌های مرتبط (ویکی‌پدیا)", "", "، ".join(report["entities"])]

    if report.get("errors"):
        out += ["", "## هشدارها", ""] + [f"- {e}" for e in report["errors"]]

    return "\n".join(out)
