"""The ticker: fire whatever is due, then wait.

    python -m agents.orchestrator.scheduler

Deliberately the dumbest process in the system. It does not talk to the bus,
does not know what a workflow is, and holds no state between ticks: it asks the
database what is due, stages the requests in the outbox, and sleeps. The relay
carries them from there, exactly as it does for every other producer.

That is what makes running two of these safe. The claim is
`FOR UPDATE SKIP LOCKED` inside the same transaction that moves `next_run_at`,
so a second scheduler finds nothing to do rather than starting the same audit
again.

The tick interval is a minute by default and the finest cadence is daily, so
being a few seconds late is not a thing anyone can notice — which is the point
of not building anything cleverer.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from .schedules import ScheduleStore

log = logging.getLogger(__name__)

TICK_SECONDS = float(os.environ.get("SCHEDULER_TICK_SECONDS", "60"))
BATCH = int(os.environ.get("SCHEDULER_BATCH", "20"))


def tick(store: ScheduleStore) -> int:
    """One pass. Returns how many schedules fired."""
    fired = store.fire_due(limit=BATCH)
    for row in fired:
        log.info("schedule %s started workflow %s", row["schedule_id"], row["workflow_id"])
    return len(fired)


def run(store: ScheduleStore, interval: float = TICK_SECONDS) -> None:  # pragma: no cover - loop
    log.info("scheduler ticking every %ss", interval)
    while True:
        try:
            fired = tick(store)
        except Exception:
            # A bad tick must not kill the process: the next one is a minute
            # away and the schedules are all still in the database.
            log.exception("tick failed")
            fired = 0

        # A full batch means there is probably more waiting; go straight round
        # again rather than leaving it for a minute.
        if fired < BATCH:
            time.sleep(interval)


def main() -> None:  # pragma: no cover
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("scheduler requires DATABASE_URL")
    run(ScheduleStore(dsn))


if __name__ == "__main__":  # pragma: no cover
    main()
