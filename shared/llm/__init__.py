"""LLM gateway — the only door to Claude in this project.

    from shared.llm import AIUnavailable, ask, is_available

The rule everywhere else is the same: check `is_available()` first, catch
`AIUnavailable`, and have a deterministic answer ready either way.
"""

from .client import MAX_TOKENS, MODEL, AIUnavailable, api_key, ask, is_available, parse
from .prompts import SEO_SYSTEM_PROMPT

__all__ = [
    "MAX_TOKENS",
    "MODEL",
    "SEO_SYSTEM_PROMPT",
    "AIUnavailable",
    "api_key",
    "ask",
    "is_available",
    "parse",
]
