"""The layer that writes the copy.

Same two-layer promise as the workflow summary, and this is where it earns its
keep: the rule layer produced a brief — this field, this keyword, this length
window — and a model is the only thing here that can turn a brief into a
sentence. Without a key the brief is what you get, which is a usable
instruction rather than a fabricated title.

Nothing here trusts the answer blindly. A rewrite that ignores the length
window, drops the keyword it was asked to include, or comes back empty is
discarded and the rule-layer candidate stands. The model is a better writer
than the rules; it is not a better judge of the constraints the rules set.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shared import llm

log = logging.getLogger(__name__)

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "rewrites": {
            "type": "array",
            "description": "یک مورد برای هر اصلاح خواسته‌شده، به همان ترتیب",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "field": {"type": "string", "enum": ["title", "description", "h1"]},
                    "text": {"type": "string", "description": "متن پیشنهادی، فارسی"},
                    "why_fa": {"type": "string", "description": "چرا این بهتر است، یک جمله"},
                },
                "required": ["url", "field", "text", "why_fa"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rewrites"],
    "additionalProperties": False,
}


def enrich(plans: list[dict[str, Any]], client: Any = None) -> list[dict[str, Any]]:
    """Fill in the copy the rules could not write.

    Never raises: an optimizer that fails loudly because a model was busy is
    worse than one that hands over the brief it already had.
    """
    if not plans:
        return plans
    if client is None and not llm.is_available():
        return _noted(plans, "ANTHROPIC_API_KEY تنظیم نشده؛ فقط دستورالعمل نوشته شد.")

    try:
        answer = llm.ask(
            prompt(plans),
            REWRITE_SCHEMA,
            system=llm.SEO_SYSTEM_PROMPT,
            effort="medium",
            max_tokens=8000,
            client=client,
        )
    except llm.AIUnavailable as exc:
        log.warning("keeping the rule-based plan: %s", exc)
        return _noted(plans, f"بازنویسی با مدل انجام نشد: {exc}")

    return _apply(plans, answer.get("rewrites") or [])


def _apply(plans: list[dict[str, Any]], rewrites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {
        (str(r.get("url")), str(r.get("field"))): r
        for r in rewrites if isinstance(r, dict)
    }

    for plan in plans:
        used = False
        for fix in plan["fixes"]:
            written = by_key.get((plan["url"], fix["field"]))
            if written is None:
                continue
            text = str(written.get("text") or "").strip()
            if not accepts(fix, text):
                log.info("discarding a rewrite for %s %s", plan["url"], fix["field"])
                continue
            fix["candidate"] = text
            fix["written_by"] = "ai"
            fix["why_fa"] = f"{fix['why_fa']} {written.get('why_fa', '')}".strip()
            used = True
        plan["source"] = "ai" if used else "rules"
    return plans


def accepts(fix: dict[str, Any], text: str) -> bool:
    """Whether a proposed rewrite actually satisfies the brief.

    The checks are the same constraints the rules set, applied to the answer.
    A title that is still too long, or still missing the keyword it was asked
    to include, has not fixed anything — and shipping it would mean the report
    says "fixed" about something that is not.
    """
    if not text:
        return False
    if fix.get("max_length") and len(text) > fix["max_length"]:
        return False
    if fix.get("min_length") and len(text) < fix["min_length"]:
        return False

    keyword = fix.get("keyword")
    if keyword:
        from .analyze import _covers
        return _covers(text, keyword)
    return True


def _noted(plans: list[dict[str, Any]], note: str) -> list[dict[str, Any]]:
    for plan in plans:
        plan["note"] = note
    return plans


def prompt(plans: list[dict[str, Any]]) -> str:
    """The brief, as text.

    Every constraint the answer will be checked against is stated here. Asking
    for something and then silently rejecting it for a rule that was never
    mentioned wastes a call and teaches nothing.
    """
    briefs = []
    for plan in plans:
        for fix in plan["fixes"]:
            briefs.append({
                "url": plan["url"],
                "field": fix["field"],
                "problem": fix["problem"],
                "current": fix["current"],
                "target_keyword": fix.get("keyword"),
                "min_length": fix.get("min_length"),
                "max_length": fix.get("max_length"),
            })

    return (
        "برای هر مورد زیر یک متن جایگزین بنویس.\n\n"
        f"{json.dumps(briefs, ensure_ascii=False, indent=2)}\n\n"
        "قواعدی که پاسخ با آن‌ها سنجیده می‌شود:\n"
        "- طول متن باید داخل بازه‌ی min_length و max_length باشد.\n"
        "- اگر target_keyword داده شده، باید در متن بیاید — به‌صورت طبیعی، نه چسبانده.\n"
        "- متن باید درباره‌ی همان صفحه باشد؛ چیزی که در داده‌ها نیست از خودت نساز.\n"
        "- برای هر مورد یک جمله بنویس که چرا این بهتر از فعلی است.\n"
        "- هر موردی که نمی‌توانی با این قواعد بنویسی را اصلاً برنگردان."
    )
