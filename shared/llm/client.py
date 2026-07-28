"""The single place this project talks to Claude.

Every service that wants a model call comes through `ask`. That is the point:
the refusal handling, the truncation handling, the "the model returned prose
instead of JSON" handling and the one-exception-type contract are decided once.
The alternative — each service calling the SDK directly — is how three services
end up with three different ideas of what a refusal means.

**Not verified against the live API.** This environment has no
ANTHROPIC_API_KEY, so no request here has ever been sent. What *is* tested is
everything around the call: which parameters are built, and what happens to
every shape of response — refusal, truncation, empty, non-JSON, not-an-object.
Those tests inject a stub client, so the untested surface is the one HTTP call
itself. Treat the first live run as the verification step.

Callers never see an SDK exception. They see AIUnavailable and fall back to the
rule-based path, which is the whole premise: the AI layer is optional and its
absence must never be an outage.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# Overridable so a deployment can pin a cheaper model, but the default is the
# one the prompts were written against.
MODEL = os.environ.get("SEO_LLM_MODEL") or "claude-opus-5"
MAX_TOKENS = 16000


class AIUnavailable(Exception):
    """The model layer could not answer. Callers fall back to rules only."""


def api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def is_available() -> bool:
    """Whether asking is worth attempting at all.

    Checked before building a prompt, not after: assembling a page digest for a
    hundred-page crawl and then discovering there is no key is wasted work.
    """
    if not api_key():
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _client():
    if not api_key():
        raise AIUnavailable("متغیر محیطی ANTHROPIC_API_KEY تنظیم نشده است.")
    try:
        import anthropic
    except ImportError as exc:
        raise AIUnavailable("پکیج anthropic نصب نیست: pip install anthropic") from exc
    return anthropic.Anthropic()


def ask(
    prompt: str,
    schema: dict[str, Any],
    *,
    system: str,
    effort: str = "medium",
    max_tokens: int = MAX_TOKENS,
    client: Any = None,
) -> dict[str, Any]:
    """One model call, answered as a dict matching `schema`.

    `client` exists for tests. Everything else about the request lives here so
    that a change to how we call Claude is a change to one function.
    """
    client = client or _client()

    try:
        # Streamed, not a plain create: with this max_tokens and thinking on,
        # a single response can take long enough to hit the request timeout,
        # and a timeout would be indistinguishable from the model refusing.
        with client.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            # Adaptive, not a token budget: budget_tokens is rejected outright
            # by the current models. A pre-4.6 model set through SEO_LLM_MODEL
            # would need the old form — and is not a configuration we support.
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()
    except AIUnavailable:
        raise
    except Exception as exc:
        raise AIUnavailable(f"{type(exc).__name__}: {exc}") from exc

    return parse(response)


def parse(response: Any) -> dict[str, Any]:
    """Turn a message into a dict, or say why it cannot be one.

    Split out from `ask` because this is the part worth testing exhaustively:
    every branch here is a way a live call can disappoint us.
    """
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        raise AIUnavailable("مدل به این درخواست پاسخ نداد.")
    if stop == "max_tokens":
        # The JSON is truncated, so parsing it would fail with a message about
        # syntax and hide the actual cause.
        raise AIUnavailable("پاسخ مدل به سقف توکن رسید و ناقص ماند.")

    text = next(
        (b.text for b in getattr(response, "content", []) if getattr(b, "type", None) == "text"),
        "",
    )
    if not text.strip():
        raise AIUnavailable("پاسخ مدل خالی بود.")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIUnavailable("پاسخ مدل قابل تجزیه نبود.") from exc

    if not isinstance(parsed, dict):
        # The schemas here are all objects; a bare list or string means the
        # response did not follow the schema and the caller's key lookups
        # would fail somewhere far from here.
        raise AIUnavailable(f"پاسخ مدل یک شیء JSON نبود ({type(parsed).__name__}).")
    return parsed
