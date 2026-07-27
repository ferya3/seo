"""Keyword service — HTTP API.

Same shape as the crawl service, deliberately: two entry points (HTTP and the
event bus) converging on one `run_research`, results readable by id, events
carrying a summary plus a result_url rather than the full payload.

That the second service came out looking like the first is the point — it means
the pattern generalises, and the storage and event plumbing it shares with the
crawl service now lives in `shared/`.
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
sys.path.insert(0, str(ROOT / "services" / "engine"))

from seoagent.config import KeywordConfig  # noqa: E402
from seoagent.keywords.research import research  # noqa: E402

from shared.contracts import ContractError, validate_event  # noqa: E402
from shared.db import UnknownTenant  # noqa: E402
from shared.events import Envelope, Publisher  # noqa: E402
from shared.store import PendingEvent  # noqa: E402

from .store import ResearchRecord, open_store  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "keyword-service"

store = open_store(Path(os.environ.get("KEYWORD_DATA_DIR", "/var/lib/seoagent/keywords")))
_publisher: Publisher | None = None


def publisher() -> Publisher | None:
    """Optional, so the service runs standalone over HTTP and in tests."""
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
    title="Keyword Service",
    version="1.0.0",
    description="Expands a seed term against live autocomplete and clusters the result.",
    lifespan=lifespan,
)


class ResearchRequest(BaseModel):
    seed: str = Field(min_length=1, max_length=200)
    lang: str = "fa"
    country: str = "IR"
    max_keywords: int = Field(200, ge=10, le=5000)
    include_questions: bool = True
    include_alphabet: bool = True
    include_comparisons: bool = True
    sources: list[str] = Field(default_factory=lambda: ["google", "youtube", "bing", "duckduckgo"])
    tenant_id: str | None = None
    project_id: str | None = None
    correlation_id: str | None = None


class ResearchAccepted(BaseModel):
    research_id: str
    status: str
    result_url: str


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE, "research": store.count()}


@app.post("/v1/research", response_model=ResearchAccepted, status_code=202)
def create_research(request: ResearchRequest, background: BackgroundTasks) -> ResearchAccepted:
    seed = request.seed.strip()
    if not seed:
        raise HTTPException(422, "seed must not be blank")

    research_id = str(uuid.uuid4())
    try:
        store.create(research_id, seed, tenant_id=request.tenant_id, project_id=request.project_id)
    except UnknownTenant as exc:
        # The caller named a tenant this database has never seen. Their
        # problem, not ours — 400 keeps the gateway from reporting an outage.
        raise HTTPException(400, str(exc)) from exc
    payload = request.model_dump()
    payload["seed"] = seed
    background.add_task(
        run_research,
        research_id,
        payload,
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        correlation_id=request.correlation_id,
    )
    return ResearchAccepted(
        research_id=research_id, status="queued", result_url=f"/v1/research/{research_id}"
    )


# `tenant_id` is a query parameter rather than something inferred here: this
# service has no idea who is calling, by design — the gateway authenticates and
# passes on the tenant it resolved from the token. Reads used to ignore it
# entirely, so any id was enough to read any tenant's report.
@app.get("/v1/research/{research_id}")
def get_research(research_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    record = store.get(research_id, tenant_id=tenant_id)
    if record is None:
        # Missing and not-yours answer the same way. A 403 here would confirm
        # the id exists, which is itself worth something to a guesser.
        raise HTTPException(404, "research not found")
    return record.to_dict()


@app.get("/v1/research")
def list_research(limit: int = 25, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return [r.summary() for r in store.recent(limit, tenant_id=tenant_id)]


# ------------------------------------------------------------------- the work


def run_research(
    research_id: str,
    request: dict[str, Any],
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation: Envelope | None = None,
) -> ResearchRecord:
    """Run research to completion and publish the outcome.

    Single entry point for the HTTP route and the bus worker.
    """
    # Read before marking running: a request with no seed must fail here, not
    # after a status write that would leave the run stuck as "running".
    seed = request["seed"]
    store.mark_running(research_id)
    envelope_fields = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id or research_id,
    }

    try:
        config = KeywordConfig(
            seed=seed,
            lang=request.get("lang", "fa"),
            country=request.get("country", "IR"),
            max_keywords=request.get("max_keywords", 200),
            include_questions=request.get("include_questions", True),
            include_alphabet=request.get("include_alphabet", True),
            include_comparisons=request.get("include_comparisons", True),
            sources=request.get("sources") or ["google", "youtube", "bing", "duckduckgo"],
        )
        report = research(config).to_dict()
        status, error = "completed", None
    except Exception as exc:
        log.exception("research %s failed", research_id)
        report, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"

    payload = researched_payload(research_id, seed, report, status, error)
    # Staged inside the same transaction as the status write when the store is
    # Postgres-backed, so a crash cannot leave a finished run with no event.
    staged = (
        PendingEvent(
            type="keyword.researched",
            payload=payload,
            producer=SERVICE,
            correlation_id=envelope_fields["correlation_id"],
            causation_id=causation.id if causation is not None else None,
        ),
    ) if _publishable("keyword.researched", payload) else ()

    if status == "completed":
        record = store.complete(research_id, report, events=staged)
    else:
        record = store.fail(research_id, error, events=staged)

    # A file-backed store cannot stage anything; it says so, and we publish
    # directly instead. With Postgres the relay owns this and publishing here
    # too would double every event.
    if staged and not store.stages_events:
        _emit("keyword.researched", payload, causation, envelope_fields)
    return record


def researched_payload(
    research_id: str, seed: str, report: dict[str, Any], status: str, error: str | None
) -> dict[str, Any]:
    keywords = report.get("keywords", [])
    return {
        "research_id": research_id,
        "seed": seed,
        "lang": report.get("lang", ""),
        "country": report.get("country", ""),
        "status": status,
        "error": error,
        "total": report.get("total", 0),
        "cluster_count": len(report.get("clusters", [])),
        # A preview so a subscriber can act without a second call; the full
        # set stays behind the result_url.
        "top_keywords": [
            {
                "keyword": k["keyword"],
                "demand": k["demand"],
                "opportunity": k["opportunity"],
                "intent": k["intent"],
            }
            for k in keywords[:25]
        ],
        "result_url": f"/v1/research/{research_id}",
    }


def _publishable(event_type: str, payload: dict[str, Any]) -> bool:
    """A contract violation is our bug — it must not reach a consumer, and it
    must not be written to the outbox either, where the relay would publish it
    later with no idea it was invalid."""
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
        # The result is already durable; a broker outage must not rewrite a
        # successful run as failed.
        log.exception("failed to publish %s", event_type)
