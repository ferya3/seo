-- Competitor comparisons: your crawl against theirs.
--
-- `crawl_id` is the subject — the comparison is about your site, not theirs —
-- and the competitor crawls ride along as an array so a report can be traced
-- back to the exact crawls it was built from. An array rather than a join
-- table because nothing ever queries "which comparisons used this crawl", and
-- a table nobody queries is a table nobody keeps correct.

CREATE TABLE competitor_comparisons (
    id                   UUID PRIMARY KEY,
    tenant_id            UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id           UUID REFERENCES projects(id) ON DELETE CASCADE,
    -- Text, not foreign keys: a comparison can be run against jobs held in the
    -- file-backed store, which have no rows here.
    crawl_id             TEXT NOT NULL,
    competitor_crawl_ids TEXT[] NOT NULL DEFAULT '{}',
    status               TEXT NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued','running','completed','failed')),
    -- How many competitors were actually judged against, which is not how many
    -- were asked for: a sample too small to mean anything is left out.
    compared_against     INTEGER,
    behind_on            INTEGER,
    error                TEXT,
    report               JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX competitor_comparisons_project_idx ON competitor_comparisons (project_id, created_at DESC);
CREATE INDEX competitor_comparisons_crawl_idx   ON competitor_comparisons (crawl_id);
CREATE INDEX competitor_comparisons_status_idx  ON competitor_comparisons (status)
    WHERE status IN ('queued','running');
