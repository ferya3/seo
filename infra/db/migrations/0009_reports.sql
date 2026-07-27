-- Rendered reports: the documents a user can send to someone else.
--
-- The rendered text lives in the JSONB payload rather than in columns of its
-- own. It is only ever read whole, it is the one thing here that is not
-- queryable data, and splitting HTML and Markdown into two columns would buy
-- a query nobody asks.

CREATE TABLE reports (
    id             UUID PRIMARY KEY,
    tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id     UUID REFERENCES projects(id) ON DELETE CASCADE,
    -- Text, not a foreign key: a report can describe a workflow held in a
    -- different deployment's database, and the id is what identifies it.
    workflow_id    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','running','completed','failed')),
    title          TEXT,
    summary_source TEXT CHECK (summary_source IN ('rules','ai')),
    error          TEXT,
    report         JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX reports_project_idx  ON reports (project_id, created_at DESC);
CREATE INDEX reports_workflow_idx ON reports (workflow_id);
