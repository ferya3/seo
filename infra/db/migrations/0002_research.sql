-- Keyword research runs.
--
-- Additive rather than folded into 0001: the container applies migrations in
-- filename order on first boot only, so editing 0001 would silently skip this
-- table on any volume that already exists.
--
-- `keywords` in 0001 holds the individual terms a project tracks over time.
-- This table holds one *run* — the seed, its outcome, and the full report.
-- They are separate because a run is an event with a status and an error,
-- while a keyword is a long-lived row that many runs update.

CREATE TABLE research (
    id           UUID PRIMARY KEY,
    tenant_id    UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id   UUID REFERENCES projects(id) ON DELETE CASCADE,
    seed         TEXT NOT NULL,
    lang         TEXT,
    country      TEXT,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','completed','failed')),
    total        INTEGER,
    error        TEXT,
    report       JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX research_project_idx ON research (project_id, created_at DESC);
CREATE INDEX research_status_idx  ON research (status) WHERE status IN ('queued','running');

-- Which service produced the event. Without it the relay is the only producer
-- a consumer ever sees, and "who emitted this" stops being answerable from the
-- message alone.
ALTER TABLE outbox ADD COLUMN producer TEXT NOT NULL DEFAULT 'unknown';

-- The relay claims a batch with FOR UPDATE SKIP LOCKED and orders by id, so
-- it reads the partial index in the same direction it drains. Without the
-- id ordering here the plan degrades to a heap scan once the table is large
-- and mostly published.
DROP INDEX IF EXISTS outbox_unpublished_idx;
CREATE INDEX outbox_unpublished_idx ON outbox (id) WHERE published_at IS NULL;

-- A relay that crashes mid-batch leaves rows claimed by a dead transaction;
-- Postgres releases those locks on disconnect, so no reaper is needed. What
-- does need bounding is the published tail — the relay deletes it on a
-- schedule, and this index makes that delete cheap.
CREATE INDEX outbox_published_idx ON outbox (published_at) WHERE published_at IS NOT NULL;
