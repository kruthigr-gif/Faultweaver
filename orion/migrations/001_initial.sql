CREATE TABLE IF NOT EXISTS orion_incidents (
    incident_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    environment TEXT NOT NULL,
    service TEXT NOT NULL,
    alert_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    incident_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS orion_plans (
    plan_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES orion_incidents(incident_id),
    plan_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    plan_json JSONB NOT NULL,
    UNIQUE (incident_id, plan_hash)
);

CREATE TABLE IF NOT EXISTS orion_audit_events (
    event_id BIGSERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES orion_incidents(incident_id),
    plan_id TEXT REFERENCES orion_plans(plan_id),
    event_type TEXT NOT NULL,
    actor_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS orion_audit_events_incident_idx
    ON orion_audit_events(incident_id, event_id);
