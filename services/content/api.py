"""Content service — HTTP API.

Fifth instance of the same shape, with one difference worth naming: this is the
first job that needs two upstream results rather than one. Coverage is a
question about a crawl *and* a keyword study, so a request carries both ids and
the service fetches both reports.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.contracts import ContractError, validate_event  # noqa: E402
from shared.db import UnknownTenant  # noqa: E402
from shared.events import Envelope, Publisher  # noqa: E402
from shared.store import PendingEvent  # noqa: E402

from . import analyze, source  # noqa: E402
from .store import ContentRecord, open_store  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "content-service"

store = open_store(Path(os.environ.get("CONTENT_DATA_DIR", "/var/lib/seoagent/content")))
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
    title="Content Service",
    version="1.0.0",
    description="Which researched keywords the site has a page for, and which pages compete.",
    lifespan=lifespan,
)


class ContentRequest(BaseModel):
    crawl_id: str = Field(min_length=1, max_length=64)
    research_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = None
    project_id: str | None = None
    correlation_id: str | None = None


class ContentAccepted(BaseModel):
    analysis_id: str
    status: str
    result_url: str


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE, "analyses": store.count()}


@app.post("/v1/content-analyses", response_model=ContentAccepted, status_code=202)
def create_analysis(request: ContentRequest, background: BackgroundTasks) -> ContentAccepted:
    analysis_id = str(uuid.uuid4())
    try:
        store.create(
            analysis_id, request.crawl_id,
            tenant_id=request.tenant_id, project_id=request.project_id,
        )
    except UnknownTenant as exc:
        raise HTTPException(400, str(exc)) from exc

    background.add_task(
        run_analysis, analysis_id, request.crawl_id, request.research_id,
        tenant_id=request.tenant_id, project_id=request.project_id,
        correlation_id=request.correlation_id,
    )
    return ContentAccepted(
        analysis_id=analysis_id, status="queued",
        result_url=f"/v1/content-analyses/{analysis_id}",
    )


# `tenant_id` is a query parameter because this service authenticates nobody —
# the gateway resolves the tenant from the token and passes it on.
@app.get("/v1/content-analyses/{analysis_id}")
def get_analysis(analysis_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    record = store.get(analysis_id, tenant_id=tenant_id)
    if record is None:
        raise HTTPException(404, "analysis not found")
    return record.to_dict()


@app.get("/v1/content-analyses")
def list_analyses(limit: int = 25, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return [r.summary() for r in store.recent(limit, tenant_id=tenant_id)]


# ------------------------------------------------------------------- the work


def run_analysis(
    analysis_id: str,
    crawl_id: str,
    research_id: str,
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation: Envelope | None = None,
) -> ContentRecord:
    """Fetch both reports, compare them, publish the outcome."""
    store.mark_running(analysis_id)
    envelope_fields = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id or analysis_id,
    }

    try:
        report = analyze.summarise(
            source.fetch_crawl(crawl_id, tenant_id),
            source.fetch_research(research_id, tenant_id),
        )
        status, error = "completed", None
    except Exception as exc:
        log.exception("content analysis %s failed", analysis_id)
        report, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"

    payload = analyzed_payload(analysis_id, crawl_id, research_id, report, status, error)
    staged = (
        PendingEvent(
            type="content.analyzed", payload=payload, producer=SERVICE,
            correlation_id=envelope_fields["correlation_id"],
            causation_id=causation.id if causation is not None else None,
        ),
    ) if _publishable("content.analyzed", payload) else ()

    if status == "completed":
        record = store.complete(analysis_id, report, events=staged)
    else:
        record = store.fail(analysis_id, error, events=staged)

    if staged and not store.stages_events:
        _emit("content.analyzed", payload, causation, envelope_fields)
    return record


def analyzed_payload(
    analysis_id: str, crawl_id: str, research_id: str,
    report: dict[str, Any], status: str, error: str | None,
) -> dict[str, Any]:
    """The headline plus enough of the gaps to act on. The full lists stay
    behind the result url."""
    return {
        "analysis_id": analysis_id,
        "crawl_id": crawl_id,
        "research_id": research_id,
        "status": status,
        "error": error,
        "pages": report.get("pages", 0),
        "keywords": report.get("keywords", 0),
        "covered": report.get("covered", 0),
        "coverage": report.get("coverage", 0),
        "gap_count": len(report.get("gaps") or []),
        "cannibalisation_count": len(report.get("cannibalisation") or []),
        "top_gaps": [
            {"keyword": row["keyword"], "demand": row.get("demand", 0)}
            for row in (report.get("gaps") or [])[:10]
        ],
        "result_url": f"/v1/content-analyses/{analysis_id}",
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
