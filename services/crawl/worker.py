"""Crawl service — event-bus worker.

Consumes `crawl.requested` and runs the same `run_crawl` the HTTP route uses,
so the two entry points can never diverge in behaviour.

    python -m services.crawl.worker
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
QUEUE = "crawl.requests"

# At-least-once delivery means a redelivered request would otherwise re-run a
# crawl that already ran. Bounded because the process is long-lived; the store
# is the durable check, this only avoids the cost of hitting it.
_seen: set[str] = set()
_SEEN_MAX = 10_000


def handle(envelope: Envelope) -> None:
    if envelope.id in _seen:
        log.info("skipping duplicate delivery of %s", envelope.id)
        return

    try:
        validate_event("crawl.requested", envelope.payload)
    except ContractError as exc:
        # Dead-letter rather than retry: a malformed payload will never
        # become valid on redelivery.
        raise ValueError(f"invalid crawl.requested: {exc}") from exc

    payload = dict(envelope.payload)
    crawl_id = payload.pop("crawl_id", None) or str(uuid.uuid4())

    # Resolved through the module, not bound at import: the API owns the
    # store and may replace it (tests, or a future swap to Postgres).
    existing = api.store.get(crawl_id)
    if existing is not None and existing.status in ("running", "completed"):
        log.info("crawl %s already %s, skipping", crawl_id, existing.status)
        _remember(envelope.id)
        return

    if existing is None:
        api.store.create(crawl_id, payload["start_url"])

    api.run_crawl(
        crawl_id,
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
    Consumer(QUEUE, ["crawl.requested"], handle).run()


if __name__ == "__main__":
    main()
