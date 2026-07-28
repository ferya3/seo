"""Competitor service — HTTP API.

Same two-doors shape as the rest: `POST /v1/comparisons` and the
`competitor.comparison_requested` event both land on `run_comparison`.

The one thing to know about the request: it takes crawl ids, not domains. A
competitor has to be crawled before it can be compared, and this service does
not start crawls — fanning work out to other services is the orchestrator's
job, and a service that quietly crawls three sites when you asked it to compare
them is one that surprises people. The gateway documents the two steps.
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
from .store import ComparisonRecord, open_store  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "competitor-service"

# Each competitor is a crawl report fetched over the network and held in
# memory while the comparison runs. Five is a study; fifty is a memory problem
# wearing the same request.
MAX_COMPETITORS = 8

store = open_store(Path(os.environ.get("COMPETITOR_DATA_DIR", "/var/lib/seoagent/competitor")))
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
    title="Competitor Service",
    version="1.0.0",
    description="How your site compares with the sites that outrank you.",
    lifespan=lifespan,
)


class ComparisonRequest(BaseModel):
    crawl_id: str = Field(min_length=1, max_length=64)
    competitor_crawl_ids: list[str] = Field(min_length=1, max_length=MAX_COMPETITORS)
    tenant_id: str | None = None
    project_id: str | None = None
    correlation_id: str | None = None


class ComparisonAccepted(BaseModel):
    comparison_id: str
    status: str
    result_url: str


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE, "comparisons": store.count()}


@app.post("/v1/comparisons", response_model=ComparisonAccepted, status_code=202)
def create_comparison(request: ComparisonRequest, background: BackgroundTasks) -> ComparisonAccepted:
    competitors = _distinct(request.competitor_crawl_ids)
    if request.crawl_id in competitors:
        # Comparing a site with itself produces a report saying it is exactly
        # average, which is worse than an error because it looks like a result.
        raise HTTPException(422, "a site cannot be its own competitor")

    comparison_id = str(uuid.uuid4())
    try:
        store.create(
            comparison_id, request.crawl_id,
            tenant_id=request.tenant_id, project_id=request.project_id,
        )
    except UnknownTenant as exc:
        raise HTTPException(400, str(exc)) from exc

    background.add_task(
        run_comparison, comparison_id, request.crawl_id, competitors,
        tenant_id=request.tenant_id, project_id=request.project_id,
        correlation_id=request.correlation_id,
    )
    return ComparisonAccepted(
        comparison_id=comparison_id, status="queued",
        result_url=f"/v1/comparisons/{comparison_id}",
    )


# `tenant_id` is a query parameter because this service authenticates nobody —
# the gateway resolves the tenant from the token and passes it on.
@app.get("/v1/comparisons/{comparison_id}")
def get_comparison(comparison_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    record = store.get(comparison_id, tenant_id=tenant_id)
    if record is None:
        raise HTTPException(404, "comparison not found")
    return record.to_dict()


@app.get("/v1/comparisons")
def list_comparisons(limit: int = 25, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return [r.summary() for r in store.recent(limit, tenant_id=tenant_id)]


# ------------------------------------------------------------------- the work


def _distinct(ids: list[str]) -> list[str]:
    """Order kept, duplicates dropped: the same competitor listed twice would
    count twice towards "how many competitors use this term"."""
    return list(dict.fromkeys(i for i in ids if i))


def run_comparison(
    comparison_id: str,
    crawl_id: str,
    competitor_crawl_ids: list[str],
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation: Envelope | None = None,
) -> ComparisonRecord:
    """Fetch every crawl, compare them, publish the outcome."""
    store.mark_running(comparison_id)
    # Deduplicated here rather than only at the door, because every path —
    # HTTP, bus, a direct call — arrives through this function. The same
    # competitor listed twice would satisfy "at least two competitors use this
    # term" on its own, and one site's house style would read as a gap.
    competitor_crawl_ids = _distinct(competitor_crawl_ids)
    envelope_fields = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id or comparison_id,
    }

    try:
        mine = source.fetch_crawl(crawl_id, tenant_id)
        # A competitor whose crawl is missing is not a reason to lose the
        # whole comparison — the other three still answer the question. It is
        # named in `unavailable` so the report is not quietly thinner than it
        # looks.
        theirs, unavailable = [], []
        for competitor_id in competitor_crawl_ids:
            try:
                theirs.append(source.fetch_crawl(competitor_id, tenant_id))
            except source.UnknownRecord as exc:
                unavailable.append({"crawl_id": competitor_id, "error": str(exc)})

        report = analyze.summarise(mine, theirs)
        report["unavailable"] = unavailable
        status, error = "completed", None
    except Exception as exc:
        log.exception("comparison %s failed", comparison_id)
        report, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"

    payload = compared_payload(comparison_id, crawl_id, competitor_crawl_ids, report, status, error)
    staged = (
        PendingEvent(
            type="competitor.compared", payload=payload, producer=SERVICE,
            correlation_id=envelope_fields["correlation_id"],
            causation_id=causation.id if causation is not None else None,
        ),
    ) if _publishable("competitor.compared", payload) else ()

    if status == "completed":
        record = store.complete(comparison_id, report, events=staged)
    else:
        record = store.fail(comparison_id, error, events=staged)

    if staged and not store.stages_events:
        _emit("competitor.compared", payload, causation, envelope_fields)
    return record


def compared_payload(
    comparison_id: str, crawl_id: str, competitor_crawl_ids: list[str],
    report: dict[str, Any], status: str, error: str | None,
) -> dict[str, Any]:
    """The headline: how many were compared, where you stand, what to write.
    The profiles and full theme list stay behind the result url."""
    return {
        "comparison_id": comparison_id,
        "crawl_id": crawl_id,
        "competitor_crawl_ids": competitor_crawl_ids[:MAX_COMPETITORS],
        "status": status,
        "error": error,
        "compared_against": report.get("compared_against", 0),
        "behind_on": report.get("behind_on") or [],
        "ahead_on": report.get("ahead_on") or [],
        "missing_theme_count": len(report.get("missing_themes") or []),
        "top_missing_themes": [
            row["term"] for row in (report.get("missing_themes") or [])[:10]
        ],
        "result_url": f"/v1/comparisons/{comparison_id}",
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
