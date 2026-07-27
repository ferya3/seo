"""SERP service — HTTP API.

Third instance of the same shape: two entry points converging on one
`run_check`, results readable by id, an event carrying the standings and a url
rather than every result page.

What is different, and worth saying plainly: the provider that fetches results
could not be verified from the environment this was written in, because every
search endpoint is blocked there. Everything around it is tested. See
providers.py.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "engine"))

from shared.contracts import ContractError, validate_event  # noqa: E402
from shared.db import UnknownTenant  # noqa: E402
from shared.events import Envelope, Publisher  # noqa: E402
from shared.store import PendingEvent  # noqa: E402

from . import analyze, providers  # noqa: E402
from .store import CheckRecord, open_store  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "serp-service"

# Between queries. A rank check is dozens of requests to one host, and firing
# them back to back is what gets a client blocked — which would break the
# service far more thoroughly than being slow.
DELAY_SECONDS = float(os.environ.get("SERP_DELAY_SECONDS", "2.0"))

store = open_store(Path(os.environ.get("SERP_DATA_DIR", "/var/lib/seoagent/serp")))
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
    title="SERP Service",
    version="1.0.0",
    description="Checks where a domain ranks for a set of keywords.",
    lifespan=lifespan,
)


class CheckRequest(BaseModel):
    target_domain: str = Field(min_length=1, max_length=253)
    keywords: list[str] = Field(min_length=1, max_length=50)
    provider: str | None = None
    lang: str = "fa"
    country: str = "IR"
    tenant_id: str | None = None
    project_id: str | None = None
    correlation_id: str | None = None


class CheckAccepted(BaseModel):
    check_id: str
    status: str
    result_url: str


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE,
        "checks": store.count(),
        "provider": providers.default_name(),
    }


@app.post("/v1/checks", response_model=CheckAccepted, status_code=202)
def create_check(request: CheckRequest, background: BackgroundTasks) -> CheckAccepted:
    domain = providers.domain_of(
        request.target_domain if "//" in request.target_domain
        else f"//{request.target_domain}"
    ) or request.target_domain.strip().lower()

    keywords = [k.strip() for k in request.keywords if k.strip()]
    if not keywords:
        raise HTTPException(422, "keywords must not be blank")

    check_id = str(uuid.uuid4())
    try:
        store.create(check_id, domain, tenant_id=request.tenant_id, project_id=request.project_id)
    except UnknownTenant as exc:
        raise HTTPException(400, str(exc)) from exc

    payload = request.model_dump()
    payload["target_domain"] = domain
    payload["keywords"] = keywords
    background.add_task(
        run_check, check_id, payload,
        tenant_id=request.tenant_id, project_id=request.project_id,
        correlation_id=request.correlation_id,
    )
    return CheckAccepted(check_id=check_id, status="queued", result_url=f"/v1/checks/{check_id}")


@app.get("/v1/checks/{check_id}")
def get_check(check_id: str) -> dict[str, Any]:
    record = store.get(check_id)
    if record is None:
        raise HTTPException(404, "check not found")
    return record.to_dict()


@app.get("/v1/checks")
def list_checks(limit: int = 25) -> list[dict[str, Any]]:
    return [r.summary() for r in store.recent(limit)]


# ------------------------------------------------------------------- the work


def run_check(
    check_id: str,
    request: dict[str, Any],
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation: Envelope | None = None,
) -> CheckRecord:
    """Check every keyword and publish the outcome.

    Single entry point for the HTTP route and the bus worker.
    """
    domain = request["target_domain"]
    keywords = request["keywords"]
    store.mark_running(check_id)
    envelope_fields = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id or check_id,
    }

    try:
        fetch = providers.get(request.get("provider") or providers.default_name())
        rankings = check_keywords(
            fetch, keywords, domain,
            lang=request.get("lang", "fa"), country=request.get("country", "IR"),
        )
        report = analyze.summarise(rankings, domain)
        status, error = "completed", None
    except Exception as exc:
        log.exception("serp check %s failed", check_id)
        report, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"

    payload = checked_payload(check_id, domain, report, status, error)
    staged = (
        PendingEvent(
            type="serp.checked", payload=payload, producer=SERVICE,
            correlation_id=envelope_fields["correlation_id"],
            causation_id=causation.id if causation is not None else None,
        ),
    ) if _publishable("serp.checked", payload) else ()

    if status == "completed":
        record = store.complete(check_id, report, events=staged)
    else:
        record = store.fail(check_id, error, events=staged)

    if staged and not store.stages_events:
        _emit("serp.checked", payload, causation, envelope_fields)
    return record


def check_keywords(
    fetch, keywords: list[str], domain: str, lang: str = "fa", country: str = "IR",
    delay: float | None = None,
) -> list[analyze.Ranking]:
    """One keyword at a time, with a pause between.

    A keyword that fails is recorded and the rest continue: one bad query out
    of thirty must not throw away twenty-nine good answers.
    """
    pause = DELAY_SECONDS if delay is None else delay
    rankings: list[analyze.Ranking] = []

    for index, keyword in enumerate(keywords):
        if index and pause:
            time.sleep(pause)
        try:
            results = fetch(keyword, lang=lang, country=country)
            rankings.append(analyze.rank(keyword, results, domain))
        except Exception as exc:
            log.warning("serp lookup failed for %r: %s", keyword, exc)
            rankings.append(analyze.Ranking(
                keyword=keyword, position=None, error=f"{type(exc).__name__}: {exc}"
            ))

    return rankings


def checked_payload(
    check_id: str, domain: str, report: dict[str, Any], status: str, error: str | None
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "target_domain": domain,
        "status": status,
        "error": error,
        "keywords_checked": report.get("keywords_checked", 0),
        "keywords_ranked": report.get("keywords_ranked", 0),
        "average_position": report.get("average_position"),
        "top_competitors": report.get("top_competitors", [])[:10],
        "opportunities": [
            {"keyword": o["keyword"], "position": o["position"], "opportunity": o["opportunity"]}
            for o in report.get("opportunities", [])[:25]
        ],
        "result_url": f"/v1/checks/{check_id}",
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
