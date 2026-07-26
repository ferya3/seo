"""Outbox relay — moves staged events from Postgres onto the bus.

Runs as its own process, one per deployment (more than one is safe, see below):

    python -m shared.relay

The ordering is deliberate and cannot be reversed: publish first, then mark
published. If the process dies between the two, the event is published again on
the next pass — at-least-once, which every consumer here already handles by
keeping the envelope id. Marking first would give at-most-once, and a lost
event is not recoverable while a duplicate one is.

`FOR UPDATE SKIP LOCKED` is what makes running two relays safe: each claims a
disjoint batch, and a relay that crashes has its locks released by Postgres on
disconnect, so nothing needs reaping.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from typing import Any

from .events import Envelope, Publisher

log = logging.getLogger(__name__)
SERVICE = "outbox-relay"

CLAIM = """
SELECT id, event_id, event_type, producer, tenant_id, project_id,
       correlation_id, causation_id, payload
FROM outbox
WHERE published_at IS NULL
ORDER BY id
FOR UPDATE SKIP LOCKED
LIMIT %s
"""


def drain(conn, publisher: Publisher, batch: int = 100) -> int:
    """Publish one batch. Returns how many events went out."""
    sent = 0
    with conn.transaction():
        rows = conn.execute(CLAIM, (batch,)).fetchall()
        for row in rows:
            (row_id, event_id, event_type, producer, tenant, project,
             correlation, causation, payload) = row
            envelope = Envelope(
                id=str(event_id),
                type=event_type,
                payload=payload,
                # The originating service, not this relay. A consumer asking
                # "who emitted this" must not get "the thing that delivers
                # everything" as the answer.
                producer=producer,
                correlation_id=correlation,
                causation_id=causation,
                tenant_id=str(tenant) if tenant else None,
                project_id=str(project) if project else None,
            )
            publisher.publish(envelope)
            conn.execute("UPDATE outbox SET published_at = now() WHERE id = %s", (row_id,))
            sent += 1
    return sent


def prune(conn, keep_hours: int = 72) -> int:
    """Drop the published tail so the table does not grow without bound.

    Kept for a few days rather than deleted immediately: the rows are the only
    record of what was published, and they are the first thing worth reading
    when a consumer claims it never received something.
    """
    with conn.transaction():
        result = conn.execute(
            "DELETE FROM outbox WHERE published_at < now() - make_interval(hours => %s)",
            (keep_hours,),
        )
    return result.rowcount


def run(dsn: str | None = None, interval: float = 1.0, batch: int = 100) -> None:  # pragma: no cover
    import psycopg

    dsn = dsn or os.environ["DATABASE_URL"]
    publisher = Publisher(SERVICE)
    stopping = False

    def stop(*_: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    last_prune = 0.0
    with psycopg.connect(dsn, autocommit=True) as conn:
        log.info("relay draining outbox every %.1fs", interval)
        while not stopping:
            try:
                sent = drain(conn, publisher, batch)
                if time.monotonic() - last_prune > 3600:
                    prune(conn)
                    last_prune = time.monotonic()
            except Exception:
                # A broker or database blip must not kill the relay: the rows
                # are still there, unpublished, and the next pass retries them.
                log.exception("relay pass failed")
                sent = 0
                time.sleep(5)
            # Only sleep when the queue drained — a full batch means there is
            # more waiting and the next pass should start immediately.
            if sent < batch:
                time.sleep(interval)
    publisher.close()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
