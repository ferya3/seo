-- Rewrite plans: what to change on the pages worth changing.
--
-- `written_by` is a column rather than only a JSONB key because it is the
-- question someone asks of a list: which of these plans has real copy in it,
-- and which is a brief waiting for a model.

CREATE TABLE optimizer_plans (
    id               UUID PRIMARY KEY,
    tenant_id        UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id       UUID REFERENCES projects(id) ON DELETE CASCADE,
    -- Text, not a foreign key: a plan can be built from a crawl held in the
    -- file-backed store, which has no row here.
    crawl_id         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued','running','completed','failed')),
    pages_with_fixes INTEGER,
    fix_count        INTEGER,
    written_by       TEXT CHECK (written_by IN ('rules','ai')),
    error            TEXT,
    report           JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX optimizer_plans_project_idx ON optimizer_plans (project_id, created_at DESC);
CREATE INDEX optimizer_plans_crawl_idx   ON optimizer_plans (crawl_id);
CREATE INDEX optimizer_plans_status_idx  ON optimizer_plans (status)
    WHERE status IN ('queued','running');

-- Optimising is a sixth kind of workflow step.
ALTER TABLE workflow_steps DROP CONSTRAINT IF EXISTS workflow_steps_kind_check;
ALTER TABLE workflow_steps ADD CONSTRAINT workflow_steps_kind_check
    CHECK (kind IN ('crawl','keyword_research','serp_check','link_analysis',
                    'content_analysis','optimizer_plan'));
