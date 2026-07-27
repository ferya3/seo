"""Where the workflow comes from.

Fetched fresh from the orchestrator at render time rather than taken from the
`workflow.completed` payload, and that is deliberate. The event carries a
summary of the steps; the document needs the whole report. It also narrows a
race: the model-written summary is attached moments *after* the workflow
finishes, off the lock, so an event payload captured at completion would always
show the rule-based one.

Narrowed, not closed. A report rendered the instant a workflow completes can
still catch the rule summary before the model's lands — the document says which
one it used, and re-rendering picks up the other.
"""

from __future__ import annotations

import os
from typing import Any

from shared.upstream import ReportUnavailable, UnknownRecord
from shared.upstream import fetch_report as _fetch

__all__ = ["ReportUnavailable", "UnknownRecord", "orchestrator_url", "fetch_workflow"]


def orchestrator_url() -> str:
    return os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8210").rstrip("/")


def fetch_workflow(workflow_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """The whole workflow — status, inputs, steps and report."""
    return _fetch(
        orchestrator_url(), f"/v1/workflows/{workflow_id}", tenant_id, key=None
    )
