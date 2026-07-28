"""The two reports this service needs.

Unlike every other service here, one input is not enough: coverage is a
question about a crawl *and* a keyword study, so this fetches both. Transport
and the retryable/not-retryable decision live in `shared.upstream`.
"""

from __future__ import annotations

import os
from typing import Any

from shared.upstream import ReportUnavailable, UnknownRecord
from shared.upstream import fetch_report as _fetch

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


def fetch_research(research_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    return _fetch(keyword_service_url(), f"/v1/research/{research_id}", tenant_id)
