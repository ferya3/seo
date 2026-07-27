-- Orchestration: multi-step work that outlives any one service call.
--
-- Until now every event on the bus had a producer and no consumer. A workflow
-- is the first thing that consumes them: it asks for a crawl, waits for
-- crawl.completed, then asks for keyword research, and only then reports.
--
-- The state lives here rather than in the orchestrator's memory because the
-- gap between "asked for a crawl" and "the crawl finished" is minutes long and
-- must survive a restart, a redeploy, or a second orchestrator taking over.

CREATE TABLE workflows (
    id           UUID PRIMARY KEY,
    tenant_id    UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id   UUID REFERENCES projects(id) ON DELETE CASCADE,
    goal         TEXT NOT NULL,
    inputs       JSONB NOT NULL DEFAULT '{}'::jsonb,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','completed','failed')),
    error        TEXT,
    report       JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workflows_project_idx ON workflows (project_id, created_at DESC);
CREATE INDEX workflows_status_idx  ON workflows (status) WHERE status IN ('queued','running');

CREATE TABLE workflow_steps (
    id           BIGSERIAL PRIMARY KEY,
    workflow_id  UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('crawl','keyword_research')),
    -- Allocated by the orchestrator before dispatch, not by the service that
    -- does the work. That is what lets a completion event be matched back to
    -- the step that asked for it — correlation_id alone would not survive a
    -- service choosing its own id.
    job_id       UUID NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','dispatched','completed','failed','skipped')),
    error        TEXT,
    result       JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workflow_id, position)
);

-- The lookup on the hot path: a completion event arrives carrying a job id and
-- has to find its step. Unique because two steps claiming the same job id would
-- make that lookup ambiguous, and ambiguity here means advancing the wrong one.
CREATE UNIQUE INDEX workflow_steps_job_key ON workflow_steps (job_id);
CREATE INDEX workflow_steps_workflow_idx ON workflow_steps (workflow_id, position);
