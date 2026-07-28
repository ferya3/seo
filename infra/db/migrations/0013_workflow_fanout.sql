-- Competitor crawls inside a workflow: one step per competitor.
--
-- Two changes, both needed by the same feature.
--
-- `params` exists because until now every step's parameters could be derived
-- from the workflow's inputs — there was one crawl, and its start_url was the
-- workflow's start_url. A competitor crawl is the first step whose parameters
-- belong to the step rather than the workflow, and deriving them from
-- position ("step 3 means the second competitor") would be a rule nobody can
-- read off the row.
ALTER TABLE workflow_steps ADD COLUMN params JSONB NOT NULL DEFAULT '{}'::jsonb;

-- `competitor_crawl` is a crawl in every mechanical sense — it publishes
-- crawl.requested and finishes on crawl.completed — but it is a distinct kind
-- because two questions have different answers for it: which crawl is *yours*
-- in the report, and whether the workflow should fail when it fails. A
-- competitor's site being down is not your audit failing.
ALTER TABLE workflow_steps DROP CONSTRAINT IF EXISTS workflow_steps_kind_check;
ALTER TABLE workflow_steps ADD CONSTRAINT workflow_steps_kind_check
    CHECK (kind IN ('crawl','keyword_research','serp_check','link_analysis',
                    'content_analysis','optimizer_plan',
                    'competitor_crawl','competitor_check'));
