"""Orchestrator — event-bus worker.

    python -m agents.orchestrator.worker

Consumes three things: `workflow.requested` to start work, and
`crawl.completed` / `keyword.researched` to advance it. The last two are also
emitted by jobs no workflow asked for, which is why an event matching no step
is ignored rather than dead-lettered.
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

from . import api, engine  # noqa: E402

log = logging.getLogger(__name__)
QUEUE = "orchestrator.events"
ROUTING_KEYS = ["workflow.requested", "crawl.completed", "keyword.researched"]

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
