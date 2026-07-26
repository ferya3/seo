"""Keyword research.

Deliberately does *not* re-export the `research` function: that would shadow the
`seoagent.keywords.research` submodule and make `from seoagent.keywords import
research` mean two different things depending on import order.
"""

from .research import KeywordReport  # noqa: F401

__all__ = ["KeywordReport"]
