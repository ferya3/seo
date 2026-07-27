"""Reporting service — HTTP API.

The last service in the chain and the only one whose output is meant to leave
the system. Everything else answers JSON to another program; this answers a
document to a person.

Two entry points as usual, plus one more: `GET /v1/reports/{id}/document`
returns the file itself with the right content type, because a report that can
only be read through a JSON field is not a report anyone will send.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.contracts import ContractError, validate_event  # noqa: E402
from shared.db import UnknownTenant  # noqa: E402
from shared.events import Envelope, Publisher  # noqa: E402
from shared.store import PendingEvent  # noqa: E402

from . import render, source  # noqa: E402
from .store import DocumentRecord, open_store  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "reporting-service"

CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
}

store = open_store(Path(os.environ.get("REPORTING_DATA_DIR", "/var/lib/seoagent/reports")))
_publisher: Publisher | None = None


def publisher() -> Publisher | None:
    global _publisher
    if os.environ.get("EVENTS_ENABLED", "1").lower() in ("0", "false", "no"):
        return None
    if _publisher is None:
        _publisher = Publisher(SERVICE)
    return _publisher


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _publisher is not None:
        _publisher.close()


app = FastAPI(
    title="Reporting Service",
    version="1.0.0",
    description="Turns a finished workflow into a document someone can send.",
    lifespan=lifespan,
)


class ReportRequest(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = None
    project_id: str | None = None
    correlation_id: str | None = None


class ReportAccepted(BaseModel):
    report_id: str
    status: str
    result_url: str


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE, "reports": store.count()}


@app.post("/v1/reports", response_model=ReportAccepted, status_code=202)
def create_report(request: ReportRequest, background: BackgroundTasks) -> ReportAccepted:
    report_id = str(uuid.uuid4())
    try:
        store.create(
            report_id, request.workflow_id,
            tenant_id=request.tenant_id, project_id=request.project_id,
        )
    except UnknownTenant as exc:
        raise HTTPException(400, str(exc)) from exc

    background.add_task(
        run_report, report_id, request.workflow_id,
        tenant_id=request.tenant_id, project_id=request.project_id,
        correlation_id=request.correlation_id,
    )
    return ReportAccepted(
        report_id=report_id, status="queued", result_url=f"/v1/reports/{report_id}"
    )


# `tenant_id` is a query parameter because this service authenticates nobody —
# the gateway resolves the tenant from the token and passes it on.
@app.get("/v1/reports/{report_id}")
def get_report(report_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    record = store.get(report_id, tenant_id=tenant_id)
    if record is None:
        raise HTTPException(404, "report not found")
    return record.to_dict()


@app.get("/v1/reports/{report_id}/document")
def get_document(report_id: str, format: str = "html", tenant_id: str | None = None) -> Response:
    """The file itself.

    Served with its own content type so a browser renders it and a download
    saves it under a sensible name — the difference between a report someone
    forwards and a JSON string they have to unescape first.
    """
    record = store.get(report_id, tenant_id=tenant_id)
    if record is None:
        raise HTTPException(404, "report not found")

    text = record.document(format)
    if text is None:
        raise HTTPException(404, f"no {format} document for this report")

    return Response(
        content=text,
        media_type=CONTENT_TYPES.get(format, "text/plain; charset=utf-8"),
        headers={"Content-Disposition": f'inline; filename="seo-report-{report_id}.{format}"'},
    )


@app.get("/v1/reports")
def list_reports(limit: int = 25, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return [r.summary() for r in store.recent(limit, tenant_id=tenant_id)]


# ------------------------------------------------------------------- the work


def run_report(
    report_id: str,
    workflow_id: str,
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation: Envelope | None = None,
) -> DocumentRecord:
    """Fetch the workflow, render both formats, publish the outcome."""
    store.mark_running(report_id)
    envelope_fields = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id or workflow_id,
    }

    try:
        workflow = source.fetch_workflow(workflow_id, tenant_id)
        result = {
            "title": render.title_of(workflow),
            "workflow_status": workflow.get("status"),
            # Recorded because a document rendered seconds after a workflow
            # finishes can carry the rule summary while the model's is still
            # being written. Whoever reads the file should be able to tell.
            "summary_source": ((workflow.get("report") or {}).get("summary") or {})
            .get("source", "rules"),
            "documents": {
                "html": render.document(workflow),
                "md": render.markdown(workflow),
            },
        }
        status, error = "completed", None
    except Exception as exc:
        log.exception("report %s failed", report_id)
        result, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"

    payload = rendered_payload(report_id, workflow_id, result, status, error)
    staged = (
        PendingEvent(
            type="report.rendered", payload=payload, producer=SERVICE,
            correlation_id=envelope_fields["correlation_id"],
            causation_id=causation.id if causation is not None else None,
        ),
    ) if _publishable("report.rendered", payload) else ()

    if status == "completed":
        record = store.complete(report_id, result, events=staged)
    else:
        record = store.fail(report_id, error, events=staged)

    if staged and not store.stages_events:
        _emit("report.rendered", payload, causation, envelope_fields)
    return record


def rendered_payload(
    report_id: str, workflow_id: str, result: dict[str, Any], status: str, error: str | None
) -> dict[str, Any]:
    """Where the document is and what it covers — never the document.

    An HTML report is tens of kilobytes of markup; a message bus is not a file
    server, and a notification consumer needs a link, not a page.
    """
    documents = result.get("documents") or {}
    return {
        "report_id": report_id,
        "workflow_id": workflow_id,
        "status": status,
        "error": error,
        "title": result.get("title"),
        "summary_source": result.get("summary_source", "rules"),
        "formats": sorted(documents),
        "result_url": f"/v1/reports/{report_id}",
        "document_url": f"/v1/reports/{report_id}/document",
    }


def _publishable(event_type: str, payload: dict[str, Any]) -> bool:
    try:
        validate_event(event_type, payload)
        return True
    except ContractError:
        log.exception("refusing to publish invalid %s", event_type)
        return False


def _emit(event_type: str, payload: dict[str, Any], causation: Envelope | None, fields: dict) -> None:
    bus = publisher()
    if bus is None:
        return
    if not _publishable(event_type, payload):
        return
    try:
        if causation is not None:
            bus.publish(causation.caused(event_type, payload, SERVICE))
        else:
            bus.emit(event_type, payload, **fields)
    except Exception:
        log.exception("failed to publish %s", event_type)
