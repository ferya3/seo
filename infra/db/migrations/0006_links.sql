-- Internal link analysis: what a site's own links say about it.
--
-- One job table in the same shape as crawls/research/serp_checks, plus a table
-- of the pages worth acting on. The full graph stays in the JSONB report: it is
-- thousands of edges per crawl, it is only ever read whole, and normalising it
-- would buy a query nobody asks.

CREATE TABLE link_analyses (
    id             UUID PRIMARY KEY,
    tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id     UUID REFERENCES projects(id) ON DELETE CASCADE,
    -- Text, not a foreign key to crawls: an analysis can be run against a
    -- crawl held in the file-backed store, which has no row here.
    crawl_id       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','running','completed','failed')),
    pages          INTEGER,
    internal_links INTEGER,
    orphan_count   INTEGER,
    error          TEXT,
    report         JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX link_analyses_project_idx ON link_analyses (project_id, created_at DESC);
CREATE INDEX link_analyses_crawl_idx   ON link_analyses (crawl_id);
CREATE INDEX link_analyses_status_idx  ON link_analyses (status)
    WHERE status IN ('queued','running');

-- The link analysis is a fourth kind of workflow step.
ALTER TABLE workflow_steps DROP CONSTRAINT IF EXISTS workflow_steps_kind_check;
ALTER TABLE workflow_steps ADD CONSTRAINT workflow_steps_kind_check
    CHECK (kind IN ('crawl','keyword_research','serp_check','link_analysis'));
