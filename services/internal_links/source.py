"""Where the crawl report comes from.

The first service-to-service call in this project, and worth explaining rather
than slipping in. Everything else here talks over the bus, because everything
else exchanges summaries that fit in a message. A crawl report does not: it is
megabytes, and the whole point of `crawl.completed` carrying a `result_url`
instead of the report was that it should be fetched by whoever needs it.

Kept behind a function of its own for the same reason the SERP provider is:
this is the part that needs another process running, so the analysis around it
stays testable with a dictionary.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)


class ReportUnavailable(RuntimeError):
    """The crawl report could not be fetched. Retryable."""


class UnknownCrawl(ValueError):
    """No such crawl, or not this tenant's. Not retryable."""


def crawl_service_url() -> str:
    return os.environ.get("CRAWL_SERVICE_URL", "http://127.0.0.1:8000").rstrip("/")


def fetch_report(crawl_id: str, tenant_id: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """Read a finished crawl's full report.

    The tenant travels with the request. Without it this service would be a way
    to read any tenant's crawl by id — the exact hole that was just closed at
    the gateway, reopened one layer down.
    """
    url = f"{crawl_service_url()}/v1/crawls/{crawl_id}"
    params = {"tenant_id": tenant_id} if tenant_id else {}

    try:
        response = requests.get(url, params=params, timeout=timeout)
    except Exception as exc:
        raise ReportUnavailable(f"crawl service unreachable: {type(exc).__name__}: {exc}") from exc

    if response.status_code == 404:
        # Missing and not-yours are the same answer here too.
        raise UnknownCrawl(f"crawl {crawl_id} not found")
    if response.status_code >= 400:
        raise ReportUnavailable(f"crawl service returned {response.status_code}")

    try:
        body = response.json()
    except ValueError as exc:
        raise ReportUnavailable("crawl service did not return JSON") from exc

    report = body.get("report")
    if not report:
        # A crawl that is still running has a record but no report. Retrying
        # later is exactly right, so this is the retryable error.
        raise ReportUnavailable(f"crawl {crawl_id} has no report yet (status {body.get('status')})")
    return report
