"""The executive summary of a finished workflow, in two layers.

Layer one is `deterministic`: pure, instant, and always present. It reads the
numbers the steps produced and says what happened in Persian. It runs inside
the transaction that finishes the workflow, which is exactly why it may not do
anything slow — the workflow row is locked at that moment.

Layer two is `enrich`: the same summary rewritten by Claude, with reasoning a
rule cannot do. It runs afterwards, off the lock, from the worker handling
`workflow.completed`. If there is no API key, if the model refuses, if the
network is down — layer one is what the report keeps. That ordering is the
promise the whole project is built on: the rule path always works, and the
model is an improvement on top of it, never a dependency.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shared import llm

log = logging.getLogger(__name__)

EFFORT_VALUES = ["کم", "متوسط", "زیاد"]

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "خلاصه‌ی وضعیت سئوی سایت در ۳ تا ۵ جمله، به فارسی، بر اساس همین اعداد",
        },
        "next_actions": {
            "type": "array",
            "description": "سه تا پنج اقدام بعدی به ترتیب اولویت",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "اقدام مشخص و قابل انجام، به فارسی"},
                    "why": {"type": "string", "description": "چرا، با اشاره به داده‌ی گزارش"},
                    "effort": {"type": "string", "enum": EFFORT_VALUES},
                    "impact": {"type": "string", "enum": EFFORT_VALUES},
                },
                "required": ["action", "why", "effort", "impact"],
                "additionalProperties": False,
            },
        },
        "watch_outs": {
            "type": "array",
            "description": "چیزهایی که این گزارش نمی‌تواند درباره‌شان قضاوت کند یا داده‌اش ناقص است",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "next_actions", "watch_outs"],
    "additionalProperties": False,
}


# --------------------------------------------------------------- layer one


def deterministic(report: dict[str, Any]) -> dict[str, Any]:
    """What the numbers say, with no model involved."""
    headline = report.get("headline") or {}
    rankings = report.get("rankings") or {}

    return {
        "source": "rules",
        "text_fa": " ".join(_sentences(report, headline, rankings)),
        "next_actions": _actions(report, headline, rankings),
        "watch_outs": [],
        "note": None,
    }


def _sentences(report, headline, rankings) -> list[str]:
    out: list[str] = []

    if report.get("status") == "failed":
        out.append(f"این تحلیل کامل نشد: {report.get('error') or 'دلیلی ثبت نشده است'}.")

    score, issues = headline.get("overall_score"), headline.get("total_issues")
    crawled = (report.get("crawl") or {}).get("pages_crawled")
    if score is not None:
        grade = (report.get("crawl") or {}).get("grade")
        piece = f"امتیاز کلی سئوی سایت {score} از ۱۰۰ است"
        if grade:
            piece += f" (رتبه {grade})"
        if crawled:
            piece += f" و {crawled} صفحه بررسی شد"
        out.append(piece + ".")
    if issues:
        out.append(f"موتور قوانین {issues} ایراد پیدا کرد.")

    found = headline.get("keywords_found")
    if found:
        seed = (report.get("keywords") or {}).get("seed")
        out.append(
            f"{found} کلمه‌ی کلیدی مرتبط" + (f" حول «{seed}»" if seed else "") + " استخراج شد."
        )

    checked = rankings.get("keywords_checked")
    if checked:
        ranked = headline.get("keywords_ranked") or 0
        piece = f"از {checked} کلمه‌ی بررسی‌شده، سایت روی {ranked} مورد در نتایج دیده می‌شود"
        average = headline.get("average_position")
        if average is not None:
            piece += f" و میانگین جایگاهش {average} است"
        out.append(piece + ".")

        best = rankings.get("best")
        if best:
            out.append(f"بهترین جایگاه {best.get('position')} برای «{best.get('keyword')}» است.")

    competitors = [c.get("domain") for c in (rankings.get("top_competitors") or [])[:3]]
    if competitors:
        out.append("رقبایی که بیش از همه بالاتر می‌ایستند: " + "، ".join(competitors) + ".")

    skipped = [s for s in (report.get("steps") or []) if s.get("status") == "skipped"]
    for step in skipped:
        out.append(f"مرحله‌ی «{step.get('kind')}» انجام نشد: {step.get('error')}.")

    return out or ["این تحلیل نتیجه‌ای برای گزارش کردن تولید نکرد."]


def _actions(report, headline, rankings) -> list[dict[str, str]]:
    """Ordered by what the report says is worth most, not by category.

    Opportunities already arrive sorted by how much there is to gain, so the
    first rows here are the first work to do.
    """
    actions: list[dict[str, str]] = []

    for item in (rankings.get("opportunities") or [])[:3]:
        keyword, position = item.get("keyword"), item.get("position")
        if position is None:
            actions.append({
                "action": f"برای «{keyword}» یک صفحه‌ی اختصاصی و کامل بساز.",
                "why": "سایت روی این عبارت در نتایج بررسی‌شده اصلاً دیده نمی‌شود.",
                "effort": "زیاد", "impact": "زیاد",
            })
        elif position <= 5:
            actions.append({
                "action": f"صفحه‌ی مربوط به «{keyword}» را تقویت کن تا از جایگاه {position} بالاتر بیاید.",
                "why": "نزدیک‌ترین برد ممکن؛ چند جایگاه بالاتر رفتن اینجا بیشترین کلیک را اضافه می‌کند.",
                "effort": "کم", "impact": "زیاد",
            })
        else:
            actions.append({
                "action": f"محتوای «{keyword}» را بازنویسی کن و لینک داخلی به آن بده (جایگاه فعلی {position}).",
                "why": "در صفحه‌ی اول نتایج هست ولی پایین‌تر از جایی که کلیک بگیرد.",
                "effort": "متوسط", "impact": "متوسط",
            })

    issues = headline.get("total_issues")
    if issues:
        actions.append({
            "action": f"{issues} ایراد فنی گزارش خزش را از بالای فهرست به پایین برطرف کن.",
            "why": "ایرادهای فنی سقف کار محتوایی را تعیین می‌کنند.",
            "effort": "متوسط", "impact": "زیاد",
        })

    return actions[:5]


# --------------------------------------------------------------- layer two


def enrich(report: dict[str, Any], client: Any = None) -> dict[str, Any]:
    """The AI summary, or the deterministic one plus the reason it is that one.

    Never raises. A summary is the last thing a finished workflow produces; a
    failure here must not turn a completed audit into a failed one.
    """
    base = deterministic(report)

    if client is None and not llm.is_available():
        return {**base, "note": "ANTHROPIC_API_KEY تنظیم نشده؛ خلاصه‌ی قانون‌محور استفاده شد."}

    try:
        answer = llm.ask(
            prompt(report),
            SUMMARY_SCHEMA,
            system=llm.SEO_SYSTEM_PROMPT,
            effort="medium",
            max_tokens=4000,
            client=client,
        )
    except llm.AIUnavailable as exc:
        log.warning("falling back to the rule-based summary: %s", exc)
        return {**base, "note": f"خلاصه‌ی هوش مصنوعی ساخته نشد: {exc}"}

    text = str(answer.get("summary") or "").strip()
    if not text:
        # Schema-valid but useless. Keeping it would replace a real summary
        # with an empty string, which is worse than not asking at all.
        return {**base, "note": "پاسخ مدل خلاصه‌ای نداشت؛ خلاصه‌ی قانون‌محور استفاده شد."}

    return {
        "source": "ai",
        "text_fa": text,
        "next_actions": answer.get("next_actions") or base["next_actions"],
        "watch_outs": answer.get("watch_outs") or [],
        "note": None,
    }


def prompt(report: dict[str, Any]) -> str:
    """The report, restated for a reader who cannot see the tables.

    Handed over as text rather than raw JSON for the parts that carry meaning,
    and as JSON for the lists, where the structure *is* the meaning.
    """
    headline = report.get("headline") or {}
    rankings = report.get("rankings") or {}
    keywords = report.get("keywords") or {}
    crawl = report.get("crawl") or {}

    steps = "\n".join(
        f"- مرحله {s.get('position')} ({s.get('kind')}): {s.get('status')}"
        + (f" — {s.get('error')}" if s.get("error") else "")
        for s in sorted(report.get("steps") or [], key=lambda s: s.get("position", 0))
    )

    return (
        f"هدف: {report.get('goal')}\n"
        f"وضعیت نهایی: {report.get('status')}"
        + (f" — {report.get('error')}" if report.get("error") else "")
        + "\n\n"
        f"مراحل:\n{steps or '—'}\n\n"
        "نتیجه‌ی خزش:\n"
        f"- امتیاز کلی: {headline.get('overall_score')} از ۱۰۰ (رتبه {crawl.get('grade')})\n"
        f"- تعداد ایرادها: {headline.get('total_issues')}\n"
        f"- صفحات خزیده‌شده: {crawl.get('pages_crawled')}\n\n"
        "نتیجه‌ی تحقیق کلمات کلیدی:\n"
        f"- عبارت اولیه: {keywords.get('seed') or '—'}\n"
        f"- تعداد کلمات یافت‌شده: {headline.get('keywords_found')}\n"
        f"- خوشه‌ها: {keywords.get('cluster_count')}\n"
        f"- نمونه: {'، '.join(str(k) for k in (keywords.get('top_keywords') or [])[:20]) or '—'}\n\n"
        "نتیجه‌ی بررسی جایگاه:\n"
        f"- دامنه: {rankings.get('target_domain') or '—'}\n"
        f"- بررسی‌شده: {rankings.get('keywords_checked')} | دیده‌شده در نتایج: "
        f"{headline.get('keywords_ranked')} | میانگین جایگاه: {headline.get('average_position')}\n"
        f"- فرصت‌ها: {json.dumps(rankings.get('opportunities') or [], ensure_ascii=False)}\n"
        f"- رقبا: {json.dumps(rankings.get('top_competitors') or [], ensure_ascii=False)}\n\n"
        "توجه: «جایگاه خالی» یعنی سایت در صفحه‌ی نتایجی که بررسی شد پیدا نشد، نه اینکه "
        "قطعاً در هیچ جایگاهی نیست. عدد «فرصت» هم اولویت نسبی است، نه پیش‌بینی ترافیک.\n\n"
        "بر اساس دقیقاً همین داده‌ها:\n"
        "۱) وضعیت سئوی این سایت را در ۳ تا ۵ جمله خلاصه کن.\n"
        "۲) سه تا پنج اقدام بعدی را به ترتیب اولویت بنویس، با تخمین تلاش و اثر.\n"
        "۳) بگو این گزارش درباره‌ی چه چیزهایی نمی‌تواند قضاوت کند."
    )
