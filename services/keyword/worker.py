"""Keyword service — event-bus worker.

Consumes `keyword.research_requested` and runs the same `run_research` the HTTP
route uses.

    python -m services.keyword.worker
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.contracts import ContractError, validate_event
from shared.events import Consumer, Envelope

from . import api

log = logging.getLogger(__name__)
QUEUE = "keyword.requests"

# Delivery is at-least-once, so a redelivery would otherwise re-run research
# that already ran. The store is the durable check; this just avoids the cost.
_seen: set[str] = set()
_SEEN_MAX = 10_000


def handle(envelope: Envelope) -> None:
    if envelope.id in _seen:
        log.info("skipping duplicate delivery of %s", envelope.id)
        return

    try:
        validate_event("keyword.research_requested", envelope.payload)
    except ContractError as exc:
        # Dead-letter: a malformed payload never becomes valid on redelivery.
        raise ValueError(f"invalid keyword.research_requested: {exc}") from exc

    payload = dict(envelope.payload)
    research_id = payload.pop("research_id", None) or str(uuid.uuid4())

    existing = api.store.get(research_id)
    if existing is not None and existing.status in ("running", "completed"):
        log.info("research %s already %s, skipping", research_id, existing.status)
        _remember(envelope.id)
        return

    if existing is None:
        # Tenancy comes from the envelope, not the payload: it is the column the
        # outbox copies onto the event, so a row created without it loses the
        # tenant for every downstream consumer.
        api.store.create(
            research_id,
            payload["seed"],
            tenant_id=envelope.tenant_id,
            project_id=envelope.project_id,
        )

    api.run_research(
        research_id,
        payload,
        tenant_id=envelope.tenant_id,
        project_id=envelope.project_id,
        correlation_id=envelope.correlation_id or envelope.id,
        causation=envelope,
    )
    _remember(envelope.id)


def _remember(event_id: str) -> None:
    if len(_seen) >= _SEEN_MAX:
        _seen.clear()
    _seen.add(event_id)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    Consumer(QUEUE, ["keyword.research_requested"], handle).run()


if __name__ == "__main__":
    main()
