-- Content coverage: which researched keywords the site has a page for.
--
-- The first job in this system with two upstream inputs, so it stores both ids.
-- `crawl_id` is the subject (it decides which site this is about); research_id
-- rides along as a column so a report can be traced back to the study it used.

CREATE TABLE content_analyses (
    id           UUID PRIMARY KEY,
    tenant_id    UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id   UUID REFERENCES projects(id) ON DELETE CASCADE,
    -- Text, not foreign keys: an analysis can be run against jobs held in the
    -- file-backed store, which have no rows here.
    crawl_id     TEXT NOT NULL,
    research_id  TEXT,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','completed','failed')),
    keywords     INTEGER,
    covered      INTEGER,
    coverage     NUMERIC(5,1),
    error        TEXT,
    report       JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX content_analyses_project_idx ON content_analyses (project_id, created_at DESC);
CREATE INDEX content_analyses_crawl_idx   ON content_analyses (crawl_id);
CREATE INDEX content_analyses_status_idx  ON content_analyses (status)
    WHERE status IN ('queued','running');

-- Content coverage is a fifth kind of workflow step.
ALTER TABLE workflow_steps DROP CONSTRAINT IF EXISTS workflow_steps_kind_check;
ALTER TABLE workflow_steps ADD CONSTRAINT workflow_steps_kind_check
    CHECK (kind IN ('crawl','keyword_research','serp_check','link_analysis','content_analysis'));
