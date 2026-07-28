"""Orchestrator — HTTP API.

Same two-doors shape as the services: `POST /v1/workflows` and the
`workflow.requested` event both land on `engine.start`.

Unlike them it has no file-backed fallback. A workflow coordinates several
services over minutes; "keep the state in this process" is not a mode it can
honestly offer, so a missing DATABASE_URL is a startup failure rather than a
quiet downgrade.
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
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.db import UnknownTenant  # noqa: E402

from . import engine, planner, schedules  # noqa: E402
from .planner import UnknownGoal  # noqa: E402
from .schedules import InvalidSchedule, ScheduleStore  # noqa: E402
from .store import WorkflowStore  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "orchestrator"

_store: WorkflowStore | None = None
_schedules: ScheduleStore | None = None


def store() -> WorkflowStore:
    global _store
    if _store is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("orchestrator requires DATABASE_URL; it has no file-backed mode")
        _store = WorkflowStore(dsn)
    return _store


def schedule_store() -> ScheduleStore:
    global _schedules
    if _schedules is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("orchestrator requires DATABASE_URL; it has no file-backed mode")
        _schedules = ScheduleStore(dsn)
    return _schedules


def reset_store(new: WorkflowStore | None = None, schedules_store: ScheduleStore | None = None) -> None:
    """Test seam, and the reason the stores are not built at import time."""
    global _store, _schedules
    _store = new
    _schedules = schedules_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _store is not None:
        _store.close()
    if _schedules is not None:
        _schedules.close()


app = FastAPI(
    title="Orchestrator",
    version="1.0.0",
    description="Plans multi-step SEO work and drives it over the event bus.",
    lifespan=lifespan,
)


class WorkflowInputs(BaseModel):
    # Extra fields are refused rather than dropped. Pydantic's default is to
    # ignore what it does not recognise, which meant a caller could pass
    # track_keywords, watch it silently do nothing, and have no way to tell.
    # It also keeps this model and the workflow.requested contract, which sets
    # additionalProperties: false, from disagreeing.
    model_config = ConfigDict(extra="forbid")

    start_url: str = Field(min_length=1, max_length=2048)
    seed: str | None = Field(default=None, max_length=200)
    max_pages: int | None = Field(default=None, ge=1, le=100_000)
    max_depth: int | None = Field(default=None, ge=1, le=20)
    # How many researched keywords to rank-check. Each one is a live search
    # request, so the ceiling is a rate-limit decision.
    track_keywords: int | None = Field(default=None, ge=1, le=planner.MAX_TRACKED)
    # How many pages the rewrite plan covers. Bounded for the same reason:
    # every page is model tokens and work someone has to do.
    optimize_pages: int | None = Field(default=None, ge=1, le=planner.MAX_OPTIMIZED)
    # Sites to compare against. Each one becomes a crawl of somebody else's
    # site, run every time this workflow runs, which is why the ceiling is
    # lower than the competitor service's own.
    competitors: list[str] | None = Field(default=None, max_length=planner.MAX_COMPETITORS)
    competitor_pages: int | None = Field(default=None, ge=1, le=planner.MAX_COMPETITOR_PAGES)
    lang: str = "fa"
    country: str = "IR"


class WorkflowRequest(BaseModel):
    goal: str = "site_audit"
    inputs: WorkflowInputs
    tenant_id: str | None = None
    project_id: str | None = None


class WorkflowAccepted(BaseModel):
    workflow_id: str
    status: str
    result_url: str


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE, "workflows": store().count()}


@app.post("/v1/workflows", response_model=WorkflowAccepted, status_code=202)
def create_workflow(request: WorkflowRequest, background: BackgroundTasks) -> WorkflowAccepted:
    workflow_id = str(uuid.uuid4())
    inputs = {k: v for k, v in request.inputs.model_dump().items() if v is not None}

    # Planning happens here rather than in the background task so an
    # unplannable goal is a 422 the caller can act on, not a workflow that
    # exists only to report that it failed immediately.
    try:
        engine.planner.plan(request.goal, inputs)
    except UnknownGoal as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    background.add_task(
        run_workflow, workflow_id, request.goal, inputs, request.tenant_id, request.project_id
    )
    return WorkflowAccepted(
        workflow_id=workflow_id, status="queued", result_url=f"/v1/workflows/{workflow_id}"
    )


# `tenant_id` is a query parameter because this service does not authenticate
# anyone — the gateway resolves the tenant from the token and passes it on.
# Reads used to ignore it, so one workflow id was enough to read another
# tenant's whole audit, and the list endpoint returned everyone's.
@app.get("/v1/workflows/{workflow_id}")
def get_workflow(workflow_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    workflow = store().get(workflow_id, tenant_id=tenant_id)
    if workflow is None:
        # Missing and not-yours are the same answer on purpose: a 403 would
        # confirm the id belongs to someone.
        raise HTTPException(404, "workflow not found")
    return workflow.to_dict()


@app.get("/v1/workflows")
def list_workflows(limit: int = 25, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return [w.summary() for w in store().recent(limit, tenant_id=tenant_id)]


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = "site_audit"
    inputs: WorkflowInputs
    cadence: str = Field(pattern="^(daily|weekly|monthly)$")
    hour: int = Field(default=9, ge=0, le=23)
    weekday: int = Field(default=0, ge=0, le=6)
    day_of_month: int = Field(default=1, ge=1, le=31)
    timezone: str = schedules.DEFAULT_TIMEZONE
    tenant_id: str | None = None
    project_id: str | None = None


@app.post("/v1/schedules", status_code=201)
def create_schedule(request: ScheduleRequest) -> dict[str, Any]:
    inputs = {k: v for k, v in request.inputs.model_dump().items() if v is not None}

    # Planned once here so an unplannable schedule is refused at creation
    # rather than failing quietly at three in the morning.
    try:
        planner.plan(request.goal, inputs)
    except (UnknownGoal, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc

    try:
        schedule = schedule_store().create(
            request.goal, inputs, request.cadence, request.hour, request.weekday,
            request.day_of_month, request.timezone, request.tenant_id, request.project_id,
        )
    except InvalidSchedule as exc:
        raise HTTPException(422, str(exc)) from exc
    except UnknownTenant as exc:
        raise HTTPException(400, str(exc)) from exc

    return schedule.to_dict()


@app.get("/v1/schedules")
def list_schedules(tenant_id: str | None = None) -> list[dict[str, Any]]:
    return [s.to_dict() for s in schedule_store().owned_by(tenant_id)]


@app.get("/v1/schedules/{schedule_id}")
def get_schedule(schedule_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    schedule = schedule_store().get(schedule_id, tenant_id=tenant_id)
    if schedule is None:
        raise HTTPException(404, "schedule not found")
    return schedule.to_dict()


@app.post("/v1/schedules/{schedule_id}/pause")
def pause_schedule(schedule_id: str, active: bool = False,
                   tenant_id: str | None = None) -> dict[str, Any]:
    """Pausing rather than deleting, because a paused schedule keeps its
    history and the settings someone worked out."""
    if not schedule_store().set_active(schedule_id, active, tenant_id):
        raise HTTPException(404, "schedule not found")
    return schedule_store().get(schedule_id, tenant_id=tenant_id).to_dict()


@app.delete("/v1/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, tenant_id: str | None = None) -> None:
    if not schedule_store().delete(schedule_id, tenant_id):
        raise HTTPException(404, "schedule not found")


def run_workflow(
    workflow_id: str,
    goal: str,
    inputs: dict[str, Any],
    tenant_id: str | None = None,
    project_id: str | None = None,
):
    try:
        return engine.start(store(), workflow_id, goal, inputs, tenant_id, project_id)
    except UnknownTenant:
        # Nothing was written, so there is no row to mark failed.
        log.exception("workflow %s names a tenant this database does not have", workflow_id)
        raise
