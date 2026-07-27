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
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.db import UnknownTenant  # noqa: E402

from . import engine  # noqa: E402
from .planner import UnknownGoal  # noqa: E402
from .store import WorkflowStore  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "orchestrator"

_store: WorkflowStore | None = None


def store() -> WorkflowStore:
    global _store
    if _store is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("orchestrator requires DATABASE_URL; it has no file-backed mode")
        _store = WorkflowStore(dsn)
    return _store


def reset_store(new: WorkflowStore | None = None) -> None:
    """Test seam, and the reason the store is not built at import time."""
    global _store
    _store = new


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _store is not None:
        _store.close()


app = FastAPI(
    title="Orchestrator",
    version="1.0.0",
    description="Plans multi-step SEO work and drives it over the event bus.",
    lifespan=lifespan,
)


class WorkflowInputs(BaseModel):
    start_url: str = Field(min_length=1, max_length=2048)
    seed: str | None = Field(default=None, max_length=200)
    max_pages: int | None = Field(default=None, ge=1, le=100_000)
    max_depth: int | None = Field(default=None, ge=1, le=20)
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


@app.get("/v1/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> dict[str, Any]:
    workflow = store().get(workflow_id)
    if workflow is None:
        raise HTTPException(404, "workflow not found")
    return workflow.to_dict()


@app.get("/v1/workflows")
def list_workflows(limit: int = 25) -> list[dict[str, Any]]:
    return [w.summary() for w in store().recent(limit)]


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
