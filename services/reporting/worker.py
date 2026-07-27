"""Reporting service — event-bus worker.

    python -m services.reporting.worker

Two bindings, and the second one is the point. `report.requested` is someone
asking. `workflow.completed` is nobody asking: a finished audit gets a document
whether or not a person thought to press a button, which is the whole reason
the bus exists — the orchestrator does not know this service is here.

It is also the second consumer of `workflow.completed`; the orchestrator's own
worker takes the same event to write the model summary. Neither knows about
the other, and that is the fan-out an event bus is for.
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
QUEUE = "reporting.requests"
ROUTING_KEYS = ["report.requested", "workflow.completed"]

_seen: set[str] = set()
_SEEN_MAX = 10_000


def handle(envelope: Envelope) -> None:
    if envelope.id in _seen:
        log.info("skipping duplicate delivery of %s", envelope.id)
        return

    if envelope.type == "workflow.completed":
        _on_workflow(envelope)
    else:
        _on_request(envelope)
    _remember(envelope.id)


def _on_request(envelope: Envelope) -> None:
    try:
        validate_event("report.requested", envelope.payload)
    except ContractError as exc:
        # Dead-letter: a malformed payload never becomes valid on redelivery.
        raise ValueError(f"invalid report.requested: {exc}") from exc

    payload = dict(envelope.payload)
    _render(payload.get("report_id") or str(uuid.uuid4()), payload["workflow_id"], envelope)


def _on_workflow(envelope: Envelope) -> None:
    workflow_id = envelope.payload.get("workflow_id")
    if not workflow_id:
        log.warning("workflow.completed with no workflow_id")
        return

    # One automatic document per workflow. Without this check every redelivery
    # would render the same report again under a new id, and the list would
    # fill with copies.
    if any(record.workflow_id == workflow_id for record in api.store.recent(200)):
        log.info("workflow %s already has a report", workflow_id)
        return

    _render(str(uuid.uuid4()), workflow_id, envelope)


def _render(report_id: str, workflow_id: str, envelope: Envelope) -> None:
    existing = api.store.get(report_id)
    if existing is not None and existing.status in ("running", "completed"):
        log.info("report %s already %s, skipping", report_id, existing.status)
        return

    if existing is None:
        # Tenancy comes from the envelope, not the payload: it is the column
        # the outbox copies onto the event.
        api.store.create(
            report_id, workflow_id,
            tenant_id=envelope.tenant_id, project_id=envelope.project_id,
        )

    api.run_report(
        report_id, workflow_id,
        tenant_id=envelope.tenant_id,
        project_id=envelope.project_id,
        correlation_id=envelope.correlation_id or envelope.id,
        causation=envelope,
    )


def _remember(event_id: str) -> None:
    if len(_seen) >= _SEEN_MAX:
        _seen.clear()
    _seen.add(event_id)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    Consumer(QUEUE, ROUTING_KEYS, handle).run()


if __name__ == "__main__":
    main()
