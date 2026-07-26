-- Identity: what the gateway needs to authenticate against the shared database.
--
-- One database, not two. The gateway used to keep users in its own SQLite file
-- while `tenants` and `projects` lived here, and nothing kept them in step —
-- which is exactly how a token carrying a perfectly valid tenant_id produced a
-- foreign key violation the first time the gateway was pointed at a real
-- service. Identity and the tenancy the services enforce have to be the same
-- rows or the foreign keys are decoration.
--
-- These SQL files are authoritative for this database. The gateway reads and
-- writes these tables through Eloquent but does not own their schema, which is
-- why `users` keeps `password_hash` rather than being renamed to Laravel's
-- `password`: the column is shared, and the framework is one consumer of it.

-- Laravel and Sanctum need a few columns 0001 did not define.
ALTER TABLE users ADD COLUMN updated_at        TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN remember_token    VARCHAR(100);

-- Email is the login identifier, so it has to be present and unique. CITEXT
-- (from 0001) already makes the comparison case-insensitive, which is what
-- stops Ali@example.com and ali@example.com from becoming two accounts.
ALTER TABLE users ALTER COLUMN email SET NOT NULL;
CREATE UNIQUE INDEX users_email_key ON users (email);

-- Sanctum's token store. Defined here rather than by Laravel's own migration
-- because `tokenable_id` has to be a UUID to match users.id — the published
-- migration assumes a bigint primary key and silently would not join.
CREATE TABLE personal_access_tokens (
    id             BIGSERIAL PRIMARY KEY,
    tokenable_type TEXT NOT NULL,
    tokenable_id   UUID NOT NULL,
    name           TEXT NOT NULL,
    token          VARCHAR(64) NOT NULL UNIQUE,
    abilities      TEXT,
    last_used_at   TIMESTAMPTZ,
    expires_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ
);
CREATE INDEX personal_access_tokens_tokenable_idx
    ON personal_access_tokens (tokenable_type, tokenable_id);
CREATE INDEX personal_access_tokens_expires_idx ON personal_access_tokens (expires_at);

-- A project belongs to exactly one tenant, and its name is how a user picks it
-- out of a list, so it must be unique inside the tenant but may repeat across
-- tenants — two customers are both allowed a project called "Blog".
CREATE UNIQUE INDEX projects_tenant_name_key ON projects (tenant_id, name);
ALTER TABLE projects ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
