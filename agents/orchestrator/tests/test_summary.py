"""The workflow summary — both layers.

No database and no API key needed: the deterministic layer is a pure function
of a report, and the model layer is exercised with a stub client. What is not
covered here is the live model call, which no test in this repository can make.

    pytest agents/orchestrator/tests/test_summary.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agents.orchestrator import summary  # noqa: E402
from shared import llm  # noqa: E402

REPORT = {
    "goal": "site_audit",
    "status": "completed",
    "error": None,
    "steps": [
        {"position": 1, "kind": "crawl", "status": "completed", "error": None},
        {"position": 2, "kind": "keyword_research", "status": "completed", "error": None},
        {"position": 3, "kind": "serp_check", "status": "completed", "error": None},
    ],
    "headline": {
        "overall_score": 73,
        "total_issues": 9,
        "keywords_found": 120,
        "keywords_ranked": 4,
        "average_position": 6.5,
    },
    "crawl": {"grade": "B", "pages_crawled": 12, "overall_score": 73, "total_issues": 9},
    "keywords": {"seed": "کفش ورزشی", "total": 120, "cluster_count": 7,
                 "top_keywords": ["کفش ورزشی مردانه", "کفش پیاده‌روی"]},
    "rankings": {
        "target_domain": "example.com",
        "keywords_checked": 10,
        "keywords_ranked": 4,
        "average_position": 6.5,
        "best": {"keyword": "کفش پیاده‌روی", "position": 3, "url": "https://example.com/walk"},
        "top_competitors": [{"domain": "rival.com", "outranks_on": 6},
                            {"domain": "shop.ir", "outranks_on": 3}],
        "opportunities": [
            {"keyword": "کفش ورزشی مردانه", "position": None, "opportunity": 100},
            {"keyword": "کفش پیاده‌روی", "position": 3, "opportunity": 55},
            {"keyword": "کتانی ارزان", "position": 8, "opportunity": 40},
        ],
    },
}


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


class BrokenClient:
    def __init__(self, exc: Exception):
        self.messages = SimpleNamespace(stream=self._stream)
        self.exc = exc

    def _stream(self, **kwargs):
        raise self.exc


GOOD_ANSWER = {
    "summary": "سایت وضعیت متوسطی دارد و بیشترین فرصتش روی عبارت‌های تجاری است.",
    "next_actions": [
        {"action": "صفحه‌ی دسته‌بندی بساز", "why": "هیچ صفحه‌ای این نیت را پوشش نمی‌دهد",
         "effort": "متوسط", "impact": "زیاد"},
    ],
    "watch_outs": ["حجم جستجوی واقعی در این گزارش نیست."],
}


# ----------------------------------------------------------- the rule layer


def test_the_deterministic_summary_states_the_numbers():
    text = summary.deterministic(REPORT)["text_fa"]
    assert "73" in text and "9" in text and "120" in text
    assert "10" in text and "6.5" in text


def test_it_names_the_competitors_that_outrank_most():
    assert "rival.com" in summary.deterministic(REPORT)["text_fa"]


def test_the_first_action_is_the_biggest_opportunity():
    first = summary.deterministic(REPORT)["next_actions"][0]
    assert "کفش ورزشی مردانه" in first["action"]


def test_an_unranked_keyword_and_a_near_miss_get_different_advice():
    actions = summary.deterministic(REPORT)["next_actions"]
    unranked = next(a for a in actions if "کفش ورزشی مردانه" in a["action"])
    near = next(a for a in actions if "کفش پیاده‌روی" in a["action"])
    assert unranked["effort"] == "زیاد"          # a page that does not exist yet
    assert near["effort"] == "کم"                # already third; a push, not a build


def test_an_empty_report_still_produces_a_summary():
    result = summary.deterministic({})
    assert result["text_fa"]
    assert result["next_actions"] == []
    assert result["source"] == "rules"


def test_a_failed_workflow_says_why_in_the_first_sentence():
    text = summary.deterministic({**REPORT, "status": "failed", "error": "crawl timed out"})["text_fa"]
    assert text.startswith("این تحلیل کامل نشد")
    assert "crawl timed out" in text


def test_a_skipped_step_is_reported_as_not_done_rather_than_ignored():
    report = {**REPORT, "steps": [
        {"position": 3, "kind": "serp_check", "status": "skipped",
         "error": "research produced no keywords to rank-check"},
    ]}
    assert "serp_check" in summary.deterministic(report)["text_fa"]


def test_at_most_five_actions_are_offered():
    many = {**REPORT, "rankings": {**REPORT["rankings"], "opportunities": [
        {"keyword": f"عبارت {i}", "position": None, "opportunity": 100} for i in range(20)
    ]}}
    assert len(summary.deterministic(many)["next_actions"]) <= 5


# ----------------------------------------------------------- the model layer


def test_the_model_summary_replaces_the_rule_one():
    result = summary.enrich(REPORT, client=StubClient(GOOD_ANSWER))
    assert result["source"] == "ai"
    assert result["text_fa"] == GOOD_ANSWER["summary"]
    assert result["watch_outs"] == GOOD_ANSWER["watch_outs"]
    assert result["note"] is None


def test_the_prompt_carries_the_actual_numbers():
    """A summary written from an empty prompt would read plausibly and mean
    nothing, and nothing downstream would notice."""
    client = StubClient(GOOD_ANSWER)
    summary.enrich(REPORT, client=client)
    prompt = client.prompts[0]
    assert "73" in prompt and "example.com" in prompt and "کفش ورزشی" in prompt
    assert "rival.com" in prompt


def test_the_prompt_says_what_the_numbers_do_not_mean():
    prompt = summary.prompt(REPORT)
    assert "پیش‌بینی ترافیک" in prompt      # the opportunity score is a priority, not a forecast
    assert "پیدا نشد" in prompt             # unranked means unfound, not absent


def test_a_model_failure_keeps_the_rule_summary():
    result = summary.enrich(REPORT, client=BrokenClient(TimeoutError("no route to host")))
    assert result["source"] == "rules"
    assert result["text_fa"] == summary.deterministic(REPORT)["text_fa"]
    assert "TimeoutError" in result["note"]


def test_a_refusal_keeps_the_rule_summary():
    class Refusing(StubClient):
        def _stream(self, **kwargs):
            class _Stream:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def get_final_message(self):
                    return SimpleNamespace(stop_reason="refusal", content=[])

            return _Stream()

    result = summary.enrich(REPORT, client=Refusing(GOOD_ANSWER))
    assert result["source"] == "rules"


def test_an_empty_model_summary_is_not_used():
    result = summary.enrich(REPORT, client=StubClient({**GOOD_ANSWER, "summary": "  "}))
    assert result["source"] == "rules"
    assert result["text_fa"] == summary.deterministic(REPORT)["text_fa"]


def test_without_an_api_key_the_rule_summary_is_kept_and_says_so(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = summary.enrich(REPORT)
    assert result["source"] == "rules"
    assert "ANTHROPIC_API_KEY" in result["note"]


def test_enrich_never_raises(monkeypatch):
    """The summary is the last thing a finished workflow does. A fault here
    must not turn a completed audit into a failed one."""
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "ask", lambda *a, **kw: (_ for _ in ()).throw(
        llm.AIUnavailable("انفجار")))
    assert summary.enrich(REPORT)["source"] == "rules"


@pytest.mark.parametrize("effort", ["کم", "متوسط", "زیاد"])
def test_the_schema_offers_the_same_effort_words_the_rules_use(effort):
    assert effort in summary.SUMMARY_SCHEMA["properties"]["next_actions"]["items"][
        "properties"]["effort"]["enum"]
