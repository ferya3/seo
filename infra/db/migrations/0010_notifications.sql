-- Notifications: who gets told, and what has already been sent.
--
-- Not job tables. A channel is configuration that outlives every job, and a
-- delivery is an append-only record whose whole value is its unique index:
-- send-once is enforced here, not by a worker remembering.

CREATE TABLE notification_channels (
    id         UUID PRIMARY KEY,
    tenant_id  UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('webhook','email')),
    target     TEXT NOT NULL,
    -- Empty means every subscribable event, which is what someone setting up
    -- their first webhook actually wants.
    events     JSONB NOT NULL DEFAULT '[]'::jsonb,
    active     BOOLEAN NOT NULL DEFAULT true,
    -- Shown once at creation and never again by the API. Used to sign webhook
    -- bodies so a receiver can tell ours from anyone else's.
    secret     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notification_channels_tenant_idx ON notification_channels (tenant_id);

CREATE TABLE notification_deliveries (
    id          UUID PRIMARY KEY,
    channel_id  UUID NOT NULL REFERENCES notification_channels(id) ON DELETE CASCADE,
    -- The event id staged in the outbox: it survives a redelivery and a relay
    -- restart, which is exactly when the same email gets sent twice.
    event_id    UUID NOT NULL,
    event_type  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'sending'
                CHECK (status IN ('sending','sent','failed')),
    http_status INTEGER,
    error       TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The whole point of the table.
    UNIQUE (channel_id, event_id)
);
CREATE INDEX notification_deliveries_recent_idx
    ON notification_deliveries (created_at DESC);
