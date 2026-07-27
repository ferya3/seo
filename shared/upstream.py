"""Reading a finished report from a sibling service.

Everything in this system that fits in a message travels on the bus. Reports do
not — a crawl report is megabytes — which is why `crawl.completed` has always
carried a `result_url` rather than the report itself. Two services now follow
that url, so the decision of what each failure *means* lives here once:

  * 404 is not retryable. The id is wrong or belongs to another tenant, and
    retrying forever is how a queue fills with work that can never succeed.
  * Everything else is retryable — including a job that exists but has not
    finished, which is the case a redelivery genuinely fixes.

The tenant travels with every request. Without it these calls would be a way to
read any tenant's report by id, which is the hole closed at the gateway
reopened one layer down.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


class ReportUnavailable(RuntimeError):
    """The report could not be fetched, or is not ready. Retryable."""


class UnknownRecord(ValueError):
    """No such record, or not this tenant's. Not retryable."""


def fetch_report(
    base_url: str,
    path: str,
    tenant_id: str | None = None,
    timeout: float = 30.0,
    key: str | None = "report",
) -> dict[str, Any]:
    """GET `path` from `base_url` and return the report inside it.

    `key=None` returns the whole body. A document needs a job's status, inputs
    and steps as much as its findings, and those sit at the top level.
    """
    url = f"{base_url.rstrip('/')}{path}"
    params = {"tenant_id": tenant_id} if tenant_id else {}

    try:
        response = requests.get(url, params=params, timeout=timeout)
    except Exception as exc:
        raise ReportUnavailable(f"{url} unreachable: {type(exc).__name__}: {exc}") from exc

    if response.status_code == 404:
        # Missing and not-yours are the same answer here too.
        raise UnknownRecord(f"{path} not found")
    if response.status_code >= 400:
        raise ReportUnavailable(f"{url} returned {response.status_code}")

    try:
        body = response.json()
    except ValueError as exc:
        raise ReportUnavailable(f"{url} did not return JSON") from exc

    if key is None:
        return body

    report = body.get(key)
    if not report:
        # A job still running has a record and no report. Retrying later is
        # exactly right, so this is the retryable error.
        raise ReportUnavailable(f"{path} has no {key} yet (status {body.get('status')})")
    return report
