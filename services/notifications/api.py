"""Notifications — HTTP API.

Subscriptions, not jobs. There is nothing to start here: the work happens when
an event arrives. What this offers is the ability to say who should be told,
to see what has been sent, and to prove a channel works before waiting for a
real audit to find out it does not.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.db import UnknownTenant  # noqa: E402

from . import channels, deliver  # noqa: E402
from .store import NotificationStore  # noqa: E402

log = logging.getLogger(__name__)
SERVICE = "notifications-service"

_store: NotificationStore | None = None


def store() -> NotificationStore:
    global _store
    if _store is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            # A notification service that forgets its subscriptions on restart
            # is one that silently stops telling anyone anything, which is the
            # worst failure a service like this has.
            raise RuntimeError("notifications requires DATABASE_URL")
        _store = NotificationStore(dsn)
    return _store


def reset_store(new: NotificationStore | None = None) -> None:
    """Test seam, and the reason the store is not built at import time."""
    global _store
    _store = new


def public_base_url() -> str:
    """Where a link in a notification should point.

    The gateway's address, not this service's: the recipient is a person with
    a browser, and internal service urls are not reachable from one.
    """
    return os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _store is not None:
        _store.close()


app = FastAPI(
    title="Notifications Service",
    version="1.0.0",
    description="Tells someone when a report is ready.",
    lifespan=lifespan,
)


class ChannelRequest(BaseModel):
    kind: str = Field(pattern="^(webhook|email)$")
    target: str = Field(min_length=3, max_length=2048)
    events: list[str] = Field(default_factory=list)
    tenant_id: str | None = None
    project_id: str | None = None


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": SERVICE}


@app.post("/v1/channels", status_code=201)
def create_channel(request: ChannelRequest) -> dict[str, Any]:
    unknown = [e for e in request.events if e not in deliver.SUBSCRIBABLE]
    if unknown:
        # Refused rather than stored: a subscription to an event nobody
        # publishes is a silence nobody can explain later.
        raise HTTPException(422, f"unknown events: {unknown}")

    if request.kind == "webhook":
        try:
            from shared import netguard
            netguard.check_url(request.target)
        except Exception as exc:
            raise HTTPException(422, f"target not allowed: {exc}") from exc
    elif "@" not in request.target:
        raise HTTPException(422, "an email channel needs an email address")

    # Generated here, not accepted from the caller: a secret someone chose is
    # a secret they have reused. Returned exactly once.
    secret = secrets.token_urlsafe(32) if request.kind == "webhook" else None

    try:
        channel = store().add_channel(
            request.kind, request.target, request.tenant_id, request.project_id,
            request.events, secret,
        )
    except UnknownTenant as exc:
        raise HTTPException(400, str(exc)) from exc

    return channel.to_dict(reveal_secret=True)


@app.get("/v1/channels")
def list_channels(tenant_id: str | None = None) -> list[dict[str, Any]]:
    return [channel.to_dict() for channel in store().channels_for(tenant_id)]


@app.delete("/v1/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str, tenant_id: str | None = None) -> None:
    if not store().delete_channel(channel_id, tenant_id):
        raise HTTPException(404, "channel not found")


@app.post("/v1/channels/{channel_id}/test")
def test_channel(channel_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Send one sample notification now.

    Worth having its own endpoint: the alternative is finding out that a
    webhook url has a typo when the first real report goes missing.
    """
    channel = store().get_channel(channel_id, tenant_id)
    if channel is None:
        raise HTTPException(404, "channel not found")

    body = deliver.payload("report.rendered", {
        "report_id": "00000000-0000-0000-0000-000000000000",
        "workflow_id": "00000000-0000-0000-0000-000000000000",
        "title": "پیام آزمایشی از ایجنت سئو",
        "status": "completed",
        "formats": ["html", "md"],
        "document_url": "/v1/reports/00000000-0000-0000-0000-000000000000/document",
    }, public_base_url())

    result = channels.send(channel.as_row(), "report.rendered", body)
    return {"ok": result.ok, "status": result.status, "error": result.error}


@app.get("/v1/deliveries")
def list_deliveries(limit: int = 50, tenant_id: str | None = None) -> list[dict[str, Any]]:
    return store().deliveries(tenant_id, limit)
