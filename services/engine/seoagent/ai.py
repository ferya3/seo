"""Optional Claude-powered content suggestions.

Everything else in this project works without an API key. When ANTHROPIC_API_KEY
is present (and the `anthropic` package is installed) this layer adds the parts
a rule engine can't do: judging whether the copy on a page actually answers the
searcher's question, and drafting replacements.

What is left here is the SEO half — the schemas and the prompts. How a request
is made and how a disappointing response is handled moved to `shared/llm`, so
the orchestrator's report summary and this file cannot disagree about what a
refusal or a truncated answer means.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .models import Issue, SiteContext

# The engine runs standalone out of services/engine with its own virtualenv, so
# the repository root is not already importable the way it is for the services
# started from the top level. Adding it here keeps `python -m seoagent` working
# without teaching run.sh about the layout.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.llm import (  # noqa: E402
    MAX_TOKENS,
    MODEL,
    SEO_SYSTEM_PROMPT,
    AIUnavailable,
    ask,
    is_available,
)

__all__ = [
    "AIUnavailable",
    "MAX_TOKENS",
    "MODEL",
    "SYSTEM_PROMPT",
    "audit_suggestions",
    "is_available",
    "keyword_suggestions",
]

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "خلاصه وضعیت سئوی سایت در ۳ تا ۵ جمله، به فارسی"},
        "priorities": {
            "type": "array",
            "description": "سه تا پنج اقدام با بیشترین اثر، به ترتیب اولویت",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "اقدام مشخص و قابل انجام، به فارسی"},
                    "why": {"type": "string", "description": "چرا این اقدام مهم است، به فارسی"},
                    "effort": {"type": "string", "enum": ["کم", "متوسط", "زیاد"]},
                    "impact": {"type": "string", "enum": ["کم", "متوسط", "زیاد"]},
                },
                "required": ["action", "why", "effort", "impact"],
                "additionalProperties": False,
            },
        },
        "title_rewrites": {
            "type": "array",
            "description": "پیشنهاد بازنویسی عنوان برای صفحاتی که عنوان ضعیفی دارند",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "current": {"type": "string"},
                    "suggested": {"type": "string"},
                    "meta_description": {"type": "string"},
                },
                "required": ["url", "current", "suggested", "meta_description"],
                "additionalProperties": False,
            },
        },
        "content_gaps": {
            "type": "array",
            "description": "موضوع‌هایی که سایت پوشش نداده و برای این حوزه لازم است",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "priorities", "title_rewrites", "content_gaps"],
    "additionalProperties": False,
}

KEYWORD_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "تحلیل کوتاه فضای این کلمه کلیدی، به فارسی"},
        "angles": {
            "type": "array",
            "description": "زاویه‌های محتوایی که رقبا معمولاً از قلم می‌اندازند",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "عنوان پیشنهادی مقاله، به فارسی"},
                    "target_keyword": {"type": "string"},
                    "intent": {"type": "string", "enum": ["اطلاعاتی", "بررسی قبل از خرید", "خرید / اقدام", "محلی", "برند / ناوبری"]},
                    "outline": {"type": "array", "items": {"type": "string"}, "description": "زیرتیترهای پیشنهادی"},
                },
                "required": ["title", "target_keyword", "intent", "outline"],
                "additionalProperties": False,
            },
        },
        "questions_to_answer": {
            "type": "array",
            "description": "سؤال‌های واقعی کاربران که محتوا باید صریحاً جواب بدهد",
            "items": {"type": "string"},
        },
        "entities": {
            "type": "array",
            "description": "موجودیت‌ها و اصطلاحات تخصصی که برای پوشش معنایی کامل باید در متن بیایند",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "angles", "questions_to_answer", "entities"],
    "additionalProperties": False,
}

# Kept as a name here because callers and tests import it from this module;
# the text itself is shared, not an audit-specific prompt.
SYSTEM_PROMPT = SEO_SYSTEM_PROMPT


def _ask(prompt: str, schema: dict[str, Any], effort: str = "medium") -> dict[str, Any]:
    return ask(prompt, schema, system=SYSTEM_PROMPT, effort=effort, max_tokens=MAX_TOKENS)


def _page_digest(ctx: SiteContext, limit: int = 12) -> str:
    lines = []
    for page in ctx.indexable_pages[:limit]:
        lines.append(
            f"- {page.url}\n"
            f"  عنوان: {page.title or '—'}\n"
            f"  توضیحات: {(page.meta_description or '—')[:160]}\n"
            f"  H1: {'، '.join(page.h1s) or '—'}\n"
            f"  زیرتیترها: {'، '.join(t for lvl, t in page.headings if lvl == 2)[:300] or '—'}\n"
            f"  تعداد کلمات: {page.word_count} | لینک ورودی داخلی: {page.inlinks}\n"
            f"  ابتدای متن: {page.text[:280]}"
        )
    return "\n".join(lines)


def audit_suggestions(ctx: SiteContext, issues: list[Issue], score: int) -> dict[str, Any]:
    """Ask Claude to read the crawl and prioritise, on top of the rule findings."""
    top_issues = "\n".join(
        f"- [{i.severity.value}] {i.title_en} — {i.detail_fa[:180]} (روی {i.affected_count} صفحه)"
        for i in issues[:20]
    )
    keywords = "، ".join(ctx.target_keywords) if ctx.target_keywords else "تعیین نشده"

    prompt = (
        f"سایت مورد بررسی: {ctx.start_url}\n"
        f"امتیاز کلی سئو از ۱۰۰: {score}\n"
        f"کلمات کلیدی هدف: {keywords}\n"
        f"تعداد صفحات خزیده‌شده: {len(ctx.pages)} (قابل ایندکس: {len(ctx.indexable_pages)})\n\n"
        f"ایرادهایی که موتور قوانین پیدا کرد:\n{top_issues or '— موردی پیدا نشد'}\n\n"
        f"نمونه‌ی صفحات سایت:\n{_page_digest(ctx)}\n\n"
        "بر اساس این داده‌ها:\n"
        "۱) وضعیت سئوی سایت را خلاصه کن.\n"
        "۲) سه تا پنج اقدامی که بیشترین اثر را دارند اولویت‌بندی کن (با تخمین میزان تلاش و اثر).\n"
        "۳) برای صفحاتی که عنوان یا توضیحات ضعیفی دارند، عنوان و متا دیسکریپشن جایگزین بنویس "
        "(عنوان حداکثر ۶۰ و توضیحات حداکثر ۱۶۰ کاراکتر).\n"
        "۴) موضوع‌هایی که این سایت با توجه به حوزه‌اش باید پوشش می‌داد ولی نداده را فهرست کن."
    )
    return _ask(prompt, AUDIT_SCHEMA, effort="medium")


def keyword_suggestions(seed: str, keywords: list[dict], clusters: list[dict], trending: list[str]) -> dict[str, Any]:
    """Turn raw autocomplete data into a content angle brief."""
    top = "\n".join(
        f"- {k['keyword']} (تقاضا: {k['demand']}، نیت: {k['intent_fa']}، منابع: {'، '.join(k['sources'])})"
        for k in keywords[:60]
    )
    cluster_lines = "\n".join(
        f"- خوشه «{c['label']}» ({c['size']} عبارت، نیت غالب: {c['intent_fa']}) — کلیدواژه اصلی: {c['primary']}"
        for c in clusters[:12]
    )
    trends = "، ".join(trending[:15]) if trending else "—"

    prompt = (
        f"عبارت اولیه: «{seed}»\n\n"
        f"کلمات کلیدی استخراج‌شده از پیشنهاد خودکار موتورهای جستجو:\n{top}\n\n"
        f"خوشه‌های موضوعی:\n{cluster_lines or '—'}\n\n"
        f"ترندهای امروز گوگل در این کشور: {trends}\n\n"
        "توجه: عدد «تقاضا» تخمینی نسبی از روی داده‌ی پیشنهاد خودکار است، نه حجم جستجوی واقعی.\n\n"
        "بر اساس این داده‌ها:\n"
        "۱) فضای این کلمه کلیدی را کوتاه تحلیل کن.\n"
        "۲) زاویه‌های محتوایی پیشنهاد بده که رقبا معمولاً از قلم می‌اندازند و برای هرکدام "
        "عنوان، کلمه کلیدی هدف، نیت جستجو و ساختار زیرتیترها را بنویس.\n"
        "۳) سؤال‌های واقعی کاربران که محتوا باید صریحاً و در چند جمله‌ی اول جواب بدهد را فهرست کن.\n"
        "۴) موجودیت‌ها و اصطلاحات تخصصی لازم برای پوشش معنایی کامل این موضوع را بنویس."
    )
    return _ask(prompt, KEYWORD_SCHEMA, effort="medium")
