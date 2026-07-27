"""Optimizer service — HTTP API.

The first service that proposes rather than reports, which is why it is the
only one with a model layer inside it. The shape is otherwise the same as the
other five: two entry points on one `run_plan`, results readable by id, an
event carrying the counts and where the plan lives.

The model call happens here, in a background task, and not inside any
transaction — the same reason the workflow summary is written off the lock.
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

from . import analyze, rewrite, source  # noqa: E402
from .store import PlanRecord, open_store  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "optimizer-service"

# How many pages one plan covers. Every page is model tokens and, more to the
# point, work someone has to actually do: a hundred rewrites is a list nobody
# starts.
DEFAULT_PAGES = 5
MAX_PAGES = 25

store = open_store(Path(os.environ.get("OPTIMIZER_DATA_DIR", "/var/lib/seoagent/optimizer")))
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
    title="Optimizer Service",
    version="1.0.0",
    description="Proposes rewrites for the pages worth rewriting.",
    lifespan=lifespan,
)


class PlanRequest(BaseModel):
    crawl_id: str = Field(min_length=1, max_length=64)
    research_id: str | None = Field(default=None, max_length=64)
    pages: int = Field(default=DEFAULT_PAGES, ge=1, le=MAX_PAGES)
    tenant_id: str | None = None
    project_id: str | None = None
    correlation_id: str | None = None


class PlanAccepted(BaseModel):
    plan_id: str
    status: str
    result_url: str


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE, "plans": store.count()}


@app.post("/v1/optimizer-plans", response_model=PlanAccepted, status_code=202)
def create_plan(request: PlanRequest, background: BackgroundTasks) -> PlanAccepted:
    plan_id = str(uuid.uuid4())
    try:
        store.create(
            plan_id, request.crawl_id,
            tenant_id=request.tenant_id, project_id=request.project_id,
        )
    except UnknownTenant as exc:
        raise HTTPException(400, str(exc)) from exc

    background.add_task(
        run_plan, plan_id, request.crawl_id, request.research_id, request.pages,
        tenant_id=request.tenant_id, project_id=request.project_id,
        correlation_id=request.correlation_id,
    )
    return PlanAccepted(
        plan_id=plan_id, status="queued", result_url=f"/v1/optimizer-plans/{plan_id}"
    )


# `tenant_id` is a query parameter because this service authenticates nobody —
# the gateway resolves the tenant from the token and passes it on.
@app.get("/v1/optimizer-plans/{plan_id}")
def get_plan(plan_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    record = store.get(plan_id, tenant_id=tenant_id)
    if record is None:
        raise HTTPException(404, "plan not found")
    return record.to_dict()


@app.get("/v1/optimizer-plans")
def list_plans(limit: int = 25, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return [r.summary() for r in store.recent(limit, tenant_id=tenant_id)]


# ------------------------------------------------------------------- the work


def run_plan(
    plan_id: str,
    crawl_id: str,
    research_id: str | None = None,
    pages: int = DEFAULT_PAGES,
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation: Envelope | None = None,
) -> PlanRecord:
    """Work out what to change, then ask the model to write it."""
    store.mark_running(plan_id)
    envelope_fields = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id or plan_id,
    }

    try:
        report = analyze.summarise(
            source.fetch_crawl(crawl_id, tenant_id),
            source.fetch_research(research_id, tenant_id),
            limit=pages,
        )
        # The model only ever sees the bounded list, never every page.
        report["plans"] = rewrite.enrich(report["plans"])
        report["written_by"] = (
            "ai" if any(p.get("source") == "ai" for p in report["plans"]) else "rules"
        )
        status, error = "completed", None
    except Exception as exc:
        log.exception("optimizer plan %s failed", plan_id)
        report, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"

    payload = planned_payload(plan_id, crawl_id, report, status, error)
    staged = (
        PendingEvent(
            type="optimizer.planned", payload=payload, producer=SERVICE,
            correlation_id=envelope_fields["correlation_id"],
            causation_id=causation.id if causation is not None else None,
        ),
    ) if _publishable("optimizer.planned", payload) else ()

    if status == "completed":
        record = store.complete(plan_id, report, events=staged)
    else:
        record = store.fail(plan_id, error, events=staged)

    if staged and not store.stages_events:
        _emit("optimizer.planned", payload, causation, envelope_fields)
    return record


def planned_payload(
    plan_id: str, crawl_id: str, report: dict[str, Any], status: str, error: str | None
) -> dict[str, Any]:
    """Counts and the urls, not the copy. The rewrites are the report."""
    return {
        "plan_id": plan_id,
        "crawl_id": crawl_id,
        "status": status,
        "error": error,
        "pages_examined": report.get("pages_examined", 0),
        "pages_with_fixes": report.get("pages_with_fixes", 0),
        "fixes": report.get("fixes", 0),
        "written_by": report.get("written_by", "rules"),
        "pages": [
            {"url": plan["url"], "fixes": len(plan["fixes"])}
            for plan in (report.get("plans") or [])[:10]
        ],
        "result_url": f"/v1/optimizer-plans/{plan_id}",
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
