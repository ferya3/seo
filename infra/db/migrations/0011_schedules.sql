-- Schedules: running an audit on a cadence.
--
-- The cadence is stored as its parts rather than as a cron string. A cron
-- expression cannot say "the 31st means the end of a short month" or carry the
-- timezone the hour was meant in, and both of those are the difference between
-- a schedule that keeps its promise and one that drifts.

CREATE TABLE schedules (
    id           UUID PRIMARY KEY,
    tenant_id    UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id   UUID REFERENCES projects(id) ON DELETE CASCADE,
    goal         TEXT NOT NULL,
    inputs       JSONB NOT NULL DEFAULT '{}'::jsonb,
    cadence      TEXT NOT NULL CHECK (cadence IN ('daily','weekly','monthly')),
    hour         SMALLINT NOT NULL DEFAULT 9  CHECK (hour BETWEEN 0 AND 23),
    weekday      SMALLINT NOT NULL DEFAULT 0  CHECK (weekday BETWEEN 0 AND 6),
    day_of_month SMALLINT NOT NULL DEFAULT 1  CHECK (day_of_month BETWEEN 1 AND 31),
    -- The hour above means nothing without this.
    timezone     TEXT NOT NULL DEFAULT 'Asia/Tehran',
    active       BOOLEAN NOT NULL DEFAULT true,
    next_run_at  TIMESTAMPTZ NOT NULL,
    last_run_at  TIMESTAMPTZ,
    last_error   TEXT,
    runs         INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The scheduler's only query: what is due. Partial, because an inactive
-- schedule is never due and there is no reason to walk it every tick.
CREATE INDEX schedules_due_idx ON schedules (next_run_at) WHERE active;
CREATE INDEX schedules_tenant_idx ON schedules (tenant_id);
