"""Content service — event-bus worker.

Consumes `content.analysis_requested` and runs the same `run_analysis` the HTTP
route uses.

    python -m services.content.worker
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
QUEUE = "content.requests"

_seen: set[str] = set()
_SEEN_MAX = 10_000


def handle(envelope: Envelope) -> None:
    if envelope.id in _seen:
        log.info("skipping duplicate delivery of %s", envelope.id)
        return

    try:
        validate_event("content.analysis_requested", envelope.payload)
    except ContractError as exc:
        # Dead-letter: a malformed payload never becomes valid on redelivery.
        raise ValueError(f"invalid content.analysis_requested: {exc}") from exc

    payload = dict(envelope.payload)
    analysis_id = payload.pop("analysis_id", None) or str(uuid.uuid4())
    crawl_id, research_id = payload["crawl_id"], payload["research_id"]

    existing = api.store.get(analysis_id)
    if existing is not None and existing.status in ("running", "completed"):
        log.info("analysis %s already %s, skipping", analysis_id, existing.status)
        _remember(envelope.id)
        return

    if existing is None:
        # Tenancy comes from the envelope, not the payload: it is the column
        # the outbox copies onto the event.
        api.store.create(
            analysis_id, crawl_id,
            tenant_id=envelope.tenant_id, project_id=envelope.project_id,
        )

    api.run_analysis(
        analysis_id, crawl_id, research_id,
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
    Consumer(QUEUE, ["content.analysis_requested"], handle).run()


if __name__ == "__main__":
    main()
