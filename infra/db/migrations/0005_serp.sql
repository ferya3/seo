-- Rank tracking: where a site sits in search results for its keywords.
--
-- Two tables rather than one JSONB blob. `serp_checks` is the job, in the same
-- shape as `crawls` and `research`; `rankings` is one row per keyword per run,
-- because the question this data exists to answer is "did we move?", and that
-- is a query over time, not a document to open.

CREATE TABLE serp_checks (
    id            UUID PRIMARY KEY,
    tenant_id     UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id    UUID REFERENCES projects(id) ON DELETE CASCADE,
    target_domain TEXT NOT NULL,
    provider      TEXT,
    lang          TEXT,
    country       TEXT,
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','completed','failed')),
    keywords_checked INTEGER,
    error         TEXT,
    report        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX serp_checks_project_idx ON serp_checks (project_id, created_at DESC);
CREATE INDEX serp_checks_status_idx  ON serp_checks (status) WHERE status IN ('queued','running');

CREATE TABLE rankings (
    id            BIGSERIAL PRIMARY KEY,
    check_id      UUID NOT NULL REFERENCES serp_checks(id) ON DELETE CASCADE,
    project_id    UUID REFERENCES projects(id) ON DELETE CASCADE,
    keyword       TEXT NOT NULL,
    -- NULL means "not found in the results we fetched", which is different
    -- from position 0 and different from an error. Reporting it as a number
    -- would make "not ranking" indistinguishable from "ranking first".
    position      SMALLINT CHECK (position IS NULL OR position > 0),
    url           TEXT,
    competitors_above JSONB NOT NULL DEFAULT '[]'::jsonb,
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The history query: one keyword for one project, newest first.
CREATE INDEX rankings_history_idx ON rankings (project_id, keyword, checked_at DESC);
CREATE INDEX rankings_check_idx   ON rankings (check_id);

-- Workflows may now include a rank check. The constraint is rewritten rather
-- than dropped: it is what stops a typo in a step kind from becoming a step
-- nothing knows how to dispatch, which would strand the workflow in 'running'.
ALTER TABLE workflow_steps DROP CONSTRAINT workflow_steps_kind_check;
ALTER TABLE workflow_steps ADD CONSTRAINT workflow_steps_kind_check
    CHECK (kind IN ('crawl','keyword_research','serp_check'));
