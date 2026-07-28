"""Importing this package registers every rule module with the engine."""

from . import (
    ai_search,  # noqa: F401
    content,  # noqa: F401
    eeat,  # noqa: F401
    images,  # noqa: F401
    indexing,  # noqa: F401
    international,  # noqa: F401
    links,  # noqa: F401
    meta,  # noqa: F401
    performance,  # noqa: F401
    structured,  # noqa: F401
    technical,  # noqa: F401
)
from .base import all_rules, run_all

__all__ = ["all_rules", "run_all"]
