"""The crawls this service compares.

All of them come from the same place — the crawl service — because a
competitor's site is crawled exactly the way yours is. That is the point: two
crawls made by different code would differ for reasons that have nothing to do
with the sites.

Transport and the retryable/not-retryable decision live in `shared.upstream`.
"""

from __future__ import annotations

import os
from typing import Any

from shared.upstream import ReportUnavailable, UnknownRecord
from shared.upstream import fetch_report as _fetch

__all__ = ["ReportUnavailable", "UnknownRecord", "crawl_service_url", "fetch_crawl"]


def crawl_service_url() -> str:
    return os.environ.get("CRAWL_SERVICE_URL", "http://127.0.0.1:8000").rstrip("/")


def fetch_crawl(crawl_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    return _fetch(crawl_service_url(), f"/v1/crawls/{crawl_id}", tenant_id)
