"""Where the crawl report comes from.

The transport and the retryable/not-retryable decision live in
`shared.upstream`, because the content service needs exactly the same thing and
two copies would eventually disagree about what a 404 means. What stays here is
which service to call and under what name its errors are known.
"""

from __future__ import annotations

import os
from typing import Any

from shared.upstream import ReportUnavailable, UnknownRecord
from shared.upstream import fetch_report as _fetch

# Re-exported so callers and tests name the failure after the thing that
# failed, not after the helper.
__all__ = ["ReportUnavailable", "UnknownCrawl", "crawl_service_url", "fetch_report"]

UnknownCrawl = UnknownRecord


def crawl_service_url() -> str:
    return os.environ.get("CRAWL_SERVICE_URL", "http://127.0.0.1:8000").rstrip("/")


def fetch_report(crawl_id: str, tenant_id: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """Read a finished crawl's full report."""
    return _fetch(crawl_service_url(), f"/v1/crawls/{crawl_id}", tenant_id, timeout)
