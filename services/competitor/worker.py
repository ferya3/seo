"""Competitor service — event-bus worker.

Consumes `competitor.comparison_requested` and runs the same `run_comparison`
the HTTP route uses.

    python -m services.competitor.worker
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.contracts import ContractError, validate_event  # noqa: E402
from shared.events import Consumer, Envelope  # noqa: E402

from . import api  # noqa: E402

log = logging.getLogger(__name__)
QUEUE = "competitor.requests"

_seen: set[str] = set()
_SEEN_MAX = 10_000


def handle(envelope: Envelope) -> None:
    if envelope.id in _seen:
        log.info("skipping duplicate delivery of %s", envelope.id)
        return

    try:
        validate_event("competitor.comparison_requested", envelope.payload)
    except ContractError as exc:
        # Dead-letter: a malformed payload never becomes valid on redelivery.
        raise ValueError(f"invalid competitor.comparison_requested: {exc}") from exc

    payload = dict(envelope.payload)
    comparison_id = payload.pop("comparison_id", None) or str(uuid.uuid4())
    crawl_id = payload["crawl_id"]
    competitors = api._distinct(payload["competitor_crawl_ids"])

    existing = api.store.get(comparison_id)
    if existing is not None and existing.status in ("running", "completed"):
        log.info("comparison %s already %s, skipping", comparison_id, existing.status)
        _remember(envelope.id)
        return

    if existing is None:
        # Tenancy comes from the envelope, not the payload: it is the column
        # the outbox copies onto the event.
        api.store.create(
            comparison_id, crawl_id,
            tenant_id=envelope.tenant_id, project_id=envelope.project_id,
        )

    api.run_comparison(
        comparison_id, crawl_id, competitors,
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
    Consumer(QUEUE, ["competitor.comparison_requested"], handle).run()


if __name__ == "__main__":
    main()
