"""The model layer: what it is asked, and what is done with what comes back.

No API key exists here, so a stub client stands in. The part worth testing is
not that a model can write — it is that a rewrite which does not satisfy the
brief is thrown away rather than reported as a fix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.optimizer import analyze, rewrite  # noqa: E402
from shared import llm  # noqa: E402


def plan(field="title", problem="missing_keyword", current="بهترین انتخاب ورزشکاران",
         keyword="کفش دویدن", min_length=None, max_length=analyze.TITLE_MAX):
    return [{
        "url": "https://site.test/run",
        "keyword": keyword,
        "demand": 400,
        "words": 800,
        "source": "rules",
        "fixes": [{
            "field": field, "problem": problem, "current": current, "candidate": None,
            "keyword": keyword, "min_length": min_length, "max_length": max_length,
            "why_fa": "عبارت هدف در عنوان نیست.",
        }],
    }]


class StubClient:
    def __init__(self, answer: dict):
        self.answer = answer
        self.prompts: list[str] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        text = json.dumps(self.answer, ensure_ascii=False)

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return SimpleNamespace(
                    stop_reason="end_turn",
                    content=[SimpleNamespace(type="text", text=text)],
                )

        return _Stream()


def answer(text: str, url="https://site.test/run", field="title"):
    return {"rewrites": [{"url": url, "field": field, "text": text, "why_fa": "چون بهتر است."}]}


# ------------------------------------------------------------------ accepting


def test_a_good_rewrite_replaces_the_empty_candidate():
    plans = rewrite.enrich(plan(), client=StubClient(answer("خرید کفش دویدن حرفه‌ای مردانه")))
    fix = plans[0]["fixes"][0]

    assert fix["candidate"] == "خرید کفش دویدن حرفه‌ای مردانه"
    assert fix["written_by"] == "ai"
    assert plans[0]["source"] == "ai"


def test_a_rewrite_that_still_misses_the_keyword_is_discarded():
    """It has not fixed anything, and accepting it would make the report say
    "fixed" about something that is not."""
    plans = rewrite.enrich(plan(), client=StubClient(answer("بهترین کالاها برای ورزشکاران")))
    fix = plans[0]["fixes"][0]

    assert fix["candidate"] is None
    assert plans[0]["source"] == "rules"


def test_a_rewrite_over_the_length_limit_is_discarded():
    long_title = "خرید کفش دویدن " + "حرفه‌ای " * 12
    plans = rewrite.enrich(plan(), client=StubClient(answer(long_title)))
    assert plans[0]["fixes"][0]["candidate"] is None


def test_a_rewrite_under_the_length_floor_is_discarded():
    fix_plan = plan(field="description", problem="missing", current="",
                    min_length=analyze.DESC_MIN, max_length=analyze.DESC_MAX)
    plans = rewrite.enrich(
        fix_plan, client=StubClient(answer("کفش دویدن", field="description"))
    )
    assert plans[0]["fixes"][0]["candidate"] is None


def test_an_empty_rewrite_is_discarded():
    plans = rewrite.enrich(plan(), client=StubClient(answer("   ")))
    assert plans[0]["fixes"][0]["candidate"] is None


def test_a_rewrite_for_a_page_that_was_not_asked_about_is_ignored():
    plans = rewrite.enrich(
        plan(), client=StubClient(answer("کفش دویدن سبک", url="https://site.test/other"))
    )
    assert plans[0]["fixes"][0]["candidate"] is None


def test_a_fix_with_no_keyword_only_has_to_fit():
    fix_plan = plan(problem="too_long", current="ی" * 80, keyword=None)
    plans = rewrite.enrich(fix_plan, client=StubClient(answer("عنوان کوتاه و درست")))
    assert plans[0]["fixes"][0]["candidate"] == "عنوان کوتاه و درست"


# -------------------------------------------------------------------- asking


def test_the_prompt_states_every_rule_the_answer_is_judged_by():
    """Asking for something and then rejecting it for an unmentioned rule
    wastes a call and teaches nothing."""
    client = StubClient(answer("خرید کفش دویدن حرفه‌ای"))
    rewrite.enrich(plan(), client=client)
    prompt = client.prompts[0]

    assert "max_length" in prompt and "min_length" in prompt
    assert "target_keyword" in prompt
    assert "کفش دویدن" in prompt


def test_the_prompt_carries_the_current_text():
    client = StubClient(answer("خرید کفش دویدن حرفه‌ای"))
    rewrite.enrich(plan(), client=client)
    assert "بهترین انتخاب ورزشکاران" in client.prompts[0]


# ------------------------------------------------------------------ falling back


def test_without_a_key_the_brief_stands_and_says_why(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plans = rewrite.enrich(plan())

    assert plans[0]["source"] == "rules"
    assert "ANTHROPIC_API_KEY" in plans[0]["note"]


def test_a_model_failure_leaves_the_plan_usable(monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "ask", lambda *a, **kw: (_ for _ in ()).throw(
        llm.AIUnavailable("timeout")))

    plans = rewrite.enrich(plan())
    assert plans[0]["source"] == "rules"
    assert "timeout" in plans[0]["note"]


def test_nothing_to_rewrite_is_not_a_model_call(monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "ask", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("must not ask")))
    assert rewrite.enrich([]) == []
