"""The two reports this service needs — the same pair the content service uses.

The keyword study is optional here in a way it is not there: length and
structure fixes do not need a target keyword, so a site whose research came
back empty still gets a usable plan.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from shared.upstream import ReportUnavailable, UnknownRecord
from shared.upstream import fetch_report as _fetch

log = logging.getLogger(__name__)

__all__ = [
    "ReportUnavailable",
    "UnknownRecord",
    "crawl_service_url",
    "fetch_crawl",
    "fetch_research",
    "keyword_service_url",
]


def crawl_service_url() -> str:
    return os.environ.get("CRAWL_SERVICE_URL", "http://127.0.0.1:8000").rstrip("/")


def keyword_service_url() -> str:
    return os.environ.get("KEYWORD_SERVICE_URL", "http://127.0.0.1:8001").rstrip("/")


def fetch_crawl(crawl_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    return _fetch(crawl_service_url(), f"/v1/crawls/{crawl_id}", tenant_id)


def fetch_research(research_id: str | None, tenant_id: str | None = None) -> dict[str, Any]:
    """Empty rather than fatal when there is no study to read.

    A plan without keywords still fixes titles that are too long and pages
    with no H1; refusing to produce one would throw that away over an input
    this service can work without.
    """
    if not research_id:
        return {}
    try:
        return _fetch(keyword_service_url(), f"/v1/research/{research_id}", tenant_id)
    except (ReportUnavailable, UnknownRecord) as exc:
        log.warning("continuing without keywords: %s", exc)
        return {}
