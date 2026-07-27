"""Notifications — event-bus worker.

    python -m services.notifications.worker

The last consumer in the chain and the only one that talks to the outside
world on someone else's behalf. Everything upstream can be re-run if it goes
wrong; a notification cannot be un-sent, which is why the send-once check is a
unique index and not a set in memory.

Ordering, for one event and one channel:

  1. Claim the pair (channel, event) in the database. A conflict means another
     worker — or an earlier delivery of the same message — already has it.
  2. Send.
  3. Record the outcome. A failure worth retrying gives the claim back so the
     next delivery attempt can take it; a permanent one keeps its row as the
     record of why nobody was told.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.events import Consumer, Envelope  # noqa: E402

from . import api, channels, deliver  # noqa: E402

log = logging.getLogger(__name__)
QUEUE = "notifications.events"

# Derived from what a channel is allowed to subscribe to, so a new event type
# means one list to change rather than two that drift.
ROUTING_KEYS = list(deliver.SUBSCRIBABLE)


def handle(envelope: Envelope) -> None:
    store = api.store()
    subscribed = [
        channel for channel in store.channels_for(envelope.tenant_id)
        if deliver.wants(channel.as_row(), envelope.type)
    ]
    if not subscribed:
        log.debug("no channel wants %s for tenant %s", envelope.type, envelope.tenant_id)
        return

    body = deliver.payload(envelope.type, envelope.payload, api.public_base_url())

    for channel in subscribed:
        if not store.claim(channel.id, envelope.id, envelope.type):
            log.info("delivery of %s to %s already claimed", envelope.id, channel.id)
            continue

        result = channels.send(channel.as_row(), envelope.type, body)
        if result.ok:
            store.finish(channel.id, envelope.id, ok=True, status=result.status)
            continue

        store.finish(channel.id, envelope.id, ok=False,
                     status=result.status, error=result.error)
        if deliver.retryable(result.status, result.error):
            # Hand the pair back and let the message be redelivered. Raising
            # here would also re-send to the channels that already succeeded.
            store.release(channel.id, envelope.id)
            log.warning("retryable failure for %s: %s", channel.id, result.error)
        else:
            log.error("giving up on %s: %s", channel.id, result.error)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    Consumer(QUEUE, ROUTING_KEYS, handle).run()


if __name__ == "__main__":
    main()
