"""The LLM gateway: what it sends, and what it does with every kind of answer.

No API key exists in this environment, so a stub client stands in for the SDK.
That is not a workaround — it is the only way to test a refusal, a truncation
and a malformed answer, none of which can be provoked on demand from a live
API. The one thing left unverified is the HTTP call itself.

    pytest shared/tests/test_llm.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import llm  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def message(text: str = '{"answer": "بله"}', stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
    )


class StubClient:
    """Just enough of the SDK: a streaming context manager and a final message."""

    def __init__(self, response=None, raises: Exception | None = None):
        self.response = response if response is not None else message()
        self.raises = raises
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        response = self.response
        outer = self

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                outer.closed = True
                return False

            def get_final_message(self):
                return response

        return _Stream()


def ask(client, prompt="سلام"):
    return llm.ask(prompt, SCHEMA, system="تو یک متخصص سئو هستی", client=client)


# ------------------------------------------------------------------ the request


def test_a_good_answer_comes_back_as_a_dict():
    assert ask(StubClient()) == {"answer": "بله"}


def test_the_request_carries_the_schema_and_the_system_prompt():
    client = StubClient()
    ask(client)
    sent = client.calls[0]
    assert sent["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert sent["system"] == "تو یک متخصص سئو هستی"
    assert sent["messages"] == [{"role": "user", "content": "سلام"}]


def test_thinking_is_adaptive_rather_than_a_token_budget():
    # budget_tokens is rejected outright by the current models; sending it
    # would make every call a 400 that only shows up in production.
    client = StubClient()
    ask(client)
    assert client.calls[0]["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in json.dumps(client.calls[0])


def test_the_default_model_is_the_one_the_prompts_were_written_for():
    client = StubClient()
    ask(client)
    assert client.calls[0]["model"] == llm.MODEL


def test_the_call_is_streamed():
    # A long answer on a plain create can outlast the request timeout, and a
    # timeout is indistinguishable from the model refusing.
    client = StubClient()
    ask(client)
    assert client.closed is True


# ------------------------------------------------------------------ the answer


def test_a_refusal_is_unavailable_not_a_crash():
    with pytest.raises(llm.AIUnavailable):
        ask(StubClient(message(stop_reason="refusal")))


def test_hitting_the_token_ceiling_says_so():
    with pytest.raises(llm.AIUnavailable, match="سقف توکن"):
        ask(StubClient(message('{"answer": "نیم', stop_reason="max_tokens")))


def test_an_empty_answer_is_unavailable():
    with pytest.raises(llm.AIUnavailable, match="خالی"):
        ask(StubClient(message("   ")))


def test_prose_instead_of_json_is_unavailable():
    with pytest.raises(llm.AIUnavailable, match="تجزیه"):
        ask(StubClient(message("البته، این هم پاسخ شما:")))


def test_a_json_list_is_refused_rather_than_returned():
    # The schemas are objects. Returning a list here would fail later, at a
    # key lookup with no idea where the value came from.
    with pytest.raises(llm.AIUnavailable, match="شیء JSON"):
        ask(StubClient(message('["یک", "دو"]')))


def test_a_network_failure_is_unavailable():
    with pytest.raises(llm.AIUnavailable, match="TimeoutError"):
        ask(StubClient(raises=TimeoutError("connection reset")))


def test_no_block_of_text_at_all_is_unavailable():
    thinking_only = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="thinking", thinking="…")],
    )
    with pytest.raises(llm.AIUnavailable):
        ask(StubClient(thinking_only))


# ------------------------------------------------------------------ availability


def test_without_an_api_key_nothing_is_attempted(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.is_available() is False


def test_asking_without_a_key_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.AIUnavailable, match="ANTHROPIC_API_KEY"):
        llm.ask("سلام", SCHEMA, system="s")


def test_an_empty_api_key_counts_as_no_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert llm.api_key() is None
    assert llm.is_available() is False


def test_the_engine_and_the_gateway_share_one_implementation():
    """The engine used to own a second copy of this. Two copies meant two
    definitions of what a refusal is."""
    sys.path.insert(0, str(ROOT / "services" / "engine"))
    from seoagent import ai

    assert ai.AIUnavailable is llm.AIUnavailable
    assert ai.is_available is llm.is_available
    assert ai.SYSTEM_PROMPT == llm.SEO_SYSTEM_PROMPT
