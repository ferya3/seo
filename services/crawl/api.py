"""Crawl service — HTTP API.

Wraps the audit engine in `services/engine` as a bounded service. Two ways in:

  * POST /v1/crawls          — start a crawl, get an id back immediately
  * crawl.requested on the   — same thing, driven by the orchestrator
    event bus (see worker.py)

Both paths converge on `run_crawl`, so the API and the bus can never drift.
Results are read back with GET /v1/crawls/{id}; `crawl.completed` carries only
a summary, because a full report is megabytes and does not belong in a message.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "engine"))

from seoagent import netguard  # noqa: E402
from seoagent.config import CrawlConfig  # noqa: E402
from seoagent.crawler import Crawler  # noqa: E402
from seoagent.fetcher import normalize_url  # noqa: E402
from seoagent.report import build_report  # noqa: E402
from seoagent.rules import run_all  # noqa: E402

from shared.contracts import ContractError, validate_event  # noqa: E402
from shared.events import Envelope, Publisher  # noqa: E402

from .store import CrawlRecord, CrawlStore  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "crawl-service"

store = CrawlStore(Path(os.environ.get("CRAWL_DATA_DIR", "/var/lib/seoagent/crawls")))
_publisher: Publisher | None = None


def publisher() -> Publisher | None:
    """The bus is optional: the service is fully usable over HTTP alone, which
    is what makes it runnable in isolation and in tests."""
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
    title="Crawl Service",
    version="1.0.0",
    description="Crawls a site and scores it against the SEO rule engine.",
    lifespan=lifespan,
)


class CrawlRequest(BaseModel):
    start_url: str
    max_pages: int = Field(50, ge=1, le=100_000)
    max_depth: int = Field(3, ge=1, le=20)
    user_agent: Literal["mobile", "desktop", "googlebot"] = "mobile"
    respect_robots: bool = True
    check_external_links: bool = True
    follow_subdomains: bool = False
    target_keywords: list[str] = Field(default_factory=list, max_length=50)
    tenant_id: str | None = None
    project_id: str | None = None
    correlation_id: str | None = None


class CrawlAccepted(BaseModel):
    crawl_id: str
    status: str
    result_url: str


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE, "crawls": store.count()}


@app.post("/v1/crawls", response_model=CrawlAccepted, status_code=202)
def create_crawl(request: CrawlRequest, background: BackgroundTasks) -> CrawlAccepted:
    url = request.start_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        url = normalize_url(url)
        netguard.check_url(url)
    except netguard.TargetNotAllowed as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"invalid url: {exc}") from exc

    crawl_id = str(uuid.uuid4())
    store.create(crawl_id, url)
    payload = request.model_dump()
    payload["start_url"] = url
    background.add_task(
        run_crawl,
        crawl_id,
        payload,
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        correlation_id=request.correlation_id,
    )
    return CrawlAccepted(crawl_id=crawl_id, status="queued", result_url=f"/v1/crawls/{crawl_id}")


@app.get("/v1/crawls/{crawl_id}")
def get_crawl(crawl_id: str) -> dict[str, Any]:
    record = store.get(crawl_id)
    if record is None:
        raise HTTPException(404, "crawl not found")
    return record.to_dict()


@app.get("/v1/crawls")
def list_crawls(limit: int = 25) -> list[dict[str, Any]]:
    return [r.summary() for r in store.recent(limit)]


# ------------------------------------------------------------------- the work


def run_crawl(
    crawl_id: str,
    request: dict[str, Any],
    tenant_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation: Envelope | None = None,
) -> CrawlRecord:
    """Run a crawl to completion and publish the outcome.

    The single entry point for both the HTTP route and the bus worker.
    """
    store.mark_running(crawl_id)
    envelope_fields = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "correlation_id": correlation_id or crawl_id,
    }

    try:
        config = CrawlConfig(
            start_url=request["start_url"],
            max_pages=request.get("max_pages", 50),
            max_depth=request.get("max_depth", 3),
            user_agent_key=request.get("user_agent", "mobile"),
            respect_robots=request.get("respect_robots", True),
            check_external_links=request.get("check_external_links", True),
            follow_subdomains=request.get("follow_subdomains", False),
            target_keywords=request.get("target_keywords", []),
        )
        ctx = Crawler(config).crawl()
        report = build_report(ctx, run_all(ctx)).to_dict()
        record = store.complete(crawl_id, report)
    except Exception as exc:
        log.exception("crawl %s failed", crawl_id)
        record = store.fail(crawl_id, f"{type(exc).__name__}: {exc}")
        _emit_completed(record, causation, envelope_fields)
        return record

    _emit_completed(record, causation, envelope_fields)
    _emit_page_events(record, causation, envelope_fields)
    return record


def _emit(event_type: str, payload: dict[str, Any], causation: Envelope | None, fields: dict) -> None:
    bus = publisher()
    if bus is None:
        return
    try:
        validate_event(event_type, payload)
    except ContractError:
        # A contract violation is our bug, not the caller's. Refusing to publish
        # keeps the malformed event out of every downstream consumer.
        log.exception("refusing to publish invalid %s", event_type)
        return
    try:
        if causation is not None:
            bus.publish(causation.caused(event_type, payload, SERVICE))
        else:
            bus.emit(event_type, payload, **fields)
    except Exception:
        # A crawl that succeeded must not be reported as failed because the
        # broker was down. The result is already durable in the store.
        log.exception("failed to publish %s", event_type)


def _emit_completed(record: CrawlRecord, causation: Envelope | None, fields: dict) -> None:
    report = record.report or {}
    stats = report.get("stats", {})
    _emit(
        "crawl.completed",
        {
            "crawl_id": record.crawl_id,
            "start_url": record.start_url,
            "status": "completed" if record.status == "completed" else "failed",
            "error": record.error,
            "overall_score": report.get("overall_score", 0),
            "grade": report.get("grade", ""),
            "stats": {
                "pages_crawled": stats.get("pages_crawled", 0),
                "indexable_pages": stats.get("indexable_pages", 0),
                "errors": stats.get("errors", 0),
                "redirects": stats.get("redirects", 0),
                "avg_words": stats.get("avg_words", 0),
                "avg_load_ms": stats.get("avg_load_ms", 0),
                "total_issues": stats.get("total_issues", 0),
                "critical_count": stats.get("critical_count", 0),
                "high_count": stats.get("high_count", 0),
            },
            "category_scores": [
                {"category": c["category"], "score": c["score"], "issue_count": c["issue_count"]}
                for c in report.get("category_scores", [])
            ],
            "result_url": f"/v1/crawls/{record.crawl_id}",
        },
        causation,
        fields,
    )


def _emit_page_events(record: CrawlRecord, causation: Envelope | None, fields: dict) -> None:
    """One page.updated per indexable page, so the optimizer and internal-link
    consumers can react per page rather than re-deriving from the whole report."""
    report = record.report or {}
    for page in report.get("pages", []):
        if not page.get("indexable"):
            continue
        _emit(
            "page.updated",
            {
                "page_id": str(uuid.uuid5(uuid.NAMESPACE_URL, page["url"])),
                "crawl_id": record.crawl_id,
                "url": page["url"],
                "title": page.get("title"),
                "content_hash": page.get("content_hash", ""),
                "previous_content_hash": None,
                "word_count": page.get("words", 0),
                "status_code": page.get("status", 0),
                "indexable": True,
            },
            causation,
            fields,
        )
