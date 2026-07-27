"""Orchestrator — event-bus worker.

    python -m agents.orchestrator.worker

Consumes `workflow.requested` to start work, the services' completion events to
advance it, and its own `workflow.completed` to write the model-authored
summary once the workflow is finished and nothing holds a lock. The completion
events are also emitted by jobs no workflow asked for, which is why an event
matching no step is ignored rather than dead-lettered.
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

from . import api, engine, summary  # noqa: E402

log = logging.getLogger(__name__)
QUEUE = "orchestrator.events"

# Derived, not written out. Keeping a second list here is how the SERP step got
# added to the engine and not to the queue bindings: the orchestrator dispatched
# the rank check, the service ran it, and the completion event went nowhere —
# leaving the workflow in 'running' with nothing coming. A hand-maintained copy
# of COMPLETIONS will drift again; this cannot.
#
# workflow.completed is our own event coming back to us. That is not a loop:
# the handler for it writes a summary and publishes nothing. Doing the model
# call here rather than in the state machine is what keeps a thirty-second
# request out of a transaction holding a row lock.
ROUTING_KEYS = ["workflow.requested", "workflow.completed", *sorted(engine.COMPLETIONS)]

# Cheap guard against redelivery. The durable check is the step's status
# transition; this just avoids doing the work twice in the common case.
_seen: set[str] = set()
_SEEN_MAX = 10_000


def handle(envelope: Envelope) -> None:
    if envelope.id in _seen:
        log.info("skipping duplicate delivery of %s", envelope.id)
        return

    if envelope.type == "workflow.requested":
        _start(envelope)
    elif envelope.type == "workflow.completed":
        _summarise(envelope)
    elif envelope.type in engine.COMPLETIONS:
        engine.on_completion(api.store(), envelope.type, envelope.payload)
    else:                                       # pragma: no cover - binding decides this
        log.warning("unexpected event %s on %s", envelope.type, QUEUE)

    _remember(envelope.id)


def _start(envelope: Envelope) -> None:
    try:
        validate_event("workflow.requested", envelope.payload)
    except ContractError as exc:
        # Dead-letter: a malformed request never becomes valid on redelivery.
        raise ValueError(f"invalid workflow.requested: {exc}") from exc

    payload = dict(envelope.payload)
    workflow_id = payload.get("workflow_id") or str(uuid.uuid4())

    if api.store().get(workflow_id) is not None:
        log.info("workflow %s already exists, skipping", workflow_id)
        return

    engine.start(
        api.store(),
        workflow_id,
        payload["goal"],
        payload.get("inputs", {}),
        tenant_id=envelope.tenant_id,
        project_id=envelope.project_id,
    )


def _summarise(envelope: Envelope) -> None:
    """Rewrite a finished workflow's summary with the model, if there is one.

    Every exit here is a return, never a raise. The workflow is already
    finished and its rule-based summary is already stored; re-queueing this
    event would only spend another model call on a report that is already
    readable.
    """
    workflow_id = envelope.payload.get("workflow_id")
    workflow = api.store().get(workflow_id) if workflow_id else None
    if workflow is None:
        log.debug("workflow.completed for unknown workflow %s", workflow_id)
        return

    report = workflow.report or {}
    if (report.get("summary") or {}).get("source") == "ai":
        # A redelivery, or a second orchestrator got there first.
        return

    written = summary.enrich(report)
    if written["source"] != "ai":
        log.info("workflow %s keeps its rule-based summary: %s", workflow_id, written["note"])
        return
    api.store().save_summary(workflow_id, written)


def _remember(event_id: str) -> None:
    if len(_seen) >= _SEEN_MAX:
        _seen.clear()
    _seen.add(event_id)


def main() -> None:  # pragma: no cover
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    Consumer(QUEUE, ROUTING_KEYS, handle).run()


if __name__ == "__main__":  # pragma: no cover
    main()
