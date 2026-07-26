-- Core schema. Applied automatically by the postgres container on first boot.
--
-- Every tenant-owned table carries tenant_id and is indexed on it: that is the
-- multi-tenancy boundary, and a query that forgets it is a data leak, so the
-- column is NOT NULL everywhere rather than optional.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";

CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email         CITEXT,
    name          TEXT,
    password_hash TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX users_tenant_idx ON users (tenant_id);

CREATE TABLE projects (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    domain      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX projects_tenant_idx ON projects (tenant_id);

CREATE TABLE crawls (
    id             UUID PRIMARY KEY,
    tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id     UUID REFERENCES projects(id) ON DELETE CASCADE,
    start_url      TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','running','completed','failed')),
    overall_score  SMALLINT CHECK (overall_score BETWEEN 0 AND 100),
    error          TEXT,
    report         JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX crawls_project_idx ON crawls (project_id, created_at DESC);
CREATE INDEX crawls_status_idx  ON crawls (status) WHERE status IN ('queued','running');

CREATE TABLE pages (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id     UUID REFERENCES projects(id) ON DELETE CASCADE,
    crawl_id       UUID REFERENCES crawls(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,
    title          TEXT,
    status_code    SMALLINT,
    word_count     INTEGER,
    inlinks        INTEGER,
    depth          SMALLINT,
    indexable      BOOLEAN,
    -- Lets page.updated consumers skip a page whose content did not change.
    content_hash   TEXT,
    load_ms        INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (crawl_id, url)
);
CREATE INDEX pages_project_url_idx  ON pages (project_id, url);
CREATE INDEX pages_content_hash_idx ON pages (content_hash);

CREATE TABLE issues (
    id           BIGSERIAL PRIMARY KEY,
    crawl_id     UUID NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    rule_id      TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low','info')),
    category     TEXT NOT NULL,
    title_en     TEXT NOT NULL,
    title_fa     TEXT NOT NULL,
    detail_fa    TEXT,
    fix_fa       TEXT,
    urls         JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX issues_crawl_idx    ON issues (crawl_id);
CREATE INDEX issues_severity_idx ON issues (crawl_id, severity);

CREATE TABLE keywords (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id   UUID REFERENCES projects(id) ON DELETE CASCADE,
    keyword      TEXT NOT NULL,
    lang         TEXT NOT NULL DEFAULT 'fa',
    country      TEXT NOT NULL DEFAULT 'IR',
    intent       TEXT,
    -- Named "demand" rather than "volume": it is a relative estimate derived
    -- from autocomplete signal, not a real search volume from a paid source.
    demand       SMALLINT,
    opportunity  SMALLINT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, keyword, lang, country)
);
CREATE INDEX keywords_project_idx ON keywords (project_id);

-- Transactional outbox. A service writes its state change and the event it
-- wants published in one transaction, and a relay drains this table onto the
-- bus. Without it, a crash between "saved" and "published" silently loses the
-- event; with it, the two can never disagree.
CREATE TABLE outbox (
    id             BIGSERIAL PRIMARY KEY,
    event_id       UUID NOT NULL UNIQUE,
    event_type     TEXT NOT NULL,
    tenant_id      UUID,
    project_id     UUID,
    correlation_id TEXT,
    causation_id   TEXT,
    payload        JSONB NOT NULL,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at   TIMESTAMPTZ
);
CREATE INDEX outbox_unpublished_idx ON outbox (id) WHERE published_at IS NULL;
