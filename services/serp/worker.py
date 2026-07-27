"""SERP service — event-bus worker.

Consumes `serp.check_requested` and runs the same `run_check` the HTTP route
uses.

    python -m services.serp.worker
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
QUEUE = "serp.requests"

# Delivery is at-least-once. The store is the durable check; this avoids the
# cost of redoing dozens of lookups in the common case.
_seen: set[str] = set()
_SEEN_MAX = 10_000


def handle(envelope: Envelope) -> None:
    if envelope.id in _seen:
        log.info("skipping duplicate delivery of %s", envelope.id)
        return

    try:
        validate_event("serp.check_requested", envelope.payload)
    except ContractError as exc:
        # Dead-letter: a malformed payload never becomes valid on redelivery.
        raise ValueError(f"invalid serp.check_requested: {exc}") from exc

    payload = dict(envelope.payload)
    check_id = payload.pop("check_id", None) or str(uuid.uuid4())

    existing = api.store.get(check_id)
    if existing is not None and existing.status in ("running", "completed"):
        log.info("check %s already %s, skipping", check_id, existing.status)
        _remember(envelope.id)
        return

    if existing is None:
        # Tenancy comes from the envelope, not the payload: it is the column
        # the outbox copies onto the event.
        api.store.create(
            check_id,
            payload["target_domain"],
            tenant_id=envelope.tenant_id,
            project_id=envelope.project_id,
        )

    api.run_check(
        check_id,
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
    Consumer(QUEUE, ["serp.check_requested"], handle).run()


if __name__ == "__main__":
    main()
