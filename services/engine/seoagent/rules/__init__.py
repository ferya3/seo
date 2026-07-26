"""Importing this package registers every rule module with the engine."""

from . import (  # noqa: F401
    ai_search,
    content,
    eeat,
    images,
    indexing,
    international,
    links,
    meta,
    performance,
    structured,
    technical,
)
from .base import all_rules, run_all  # noqa: F401

__all__ = ["all_rules", "run_all"]
