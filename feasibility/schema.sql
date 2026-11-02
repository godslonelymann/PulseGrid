CREATE TABLE IF NOT EXISTS monitors (
 id uuid PRIMARY KEY, url text NOT NULL, interval_s double precision NOT NULL,
 timeout_s double precision NOT NULL, state text NOT NULL DEFAULT 'UNKNOWN',
 failures int NOT NULL DEFAULT 0, successes int NOT NULL DEFAULT 0,
 last_checked timestamptz, next_due timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, lease_id uuid
);
CREATE TABLE IF NOT EXISTS pings (
 id bigserial PRIMARY KEY, monitor_id uuid NOT NULL REFERENCES monitors(id),
 up boolean NOT NULL, latency_ms double precision NOT NULL, status_code int,
 error text, checked_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pings_monitor_time ON pings(monitor_id, checked_at);
CREATE TABLE IF NOT EXISTS incidents (
 id uuid PRIMARY KEY, monitor_id uuid NOT NULL REFERENCES monitors(id),
 opened_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident ON incidents(monitor_id) WHERE closed_at IS NULL;
CREATE TABLE IF NOT EXISTS outbox (
 id uuid PRIMARY KEY, incident_id uuid NOT NULL REFERENCES incidents(id),
 kind text NOT NULL, delivered boolean NOT NULL DEFAULT false,
 UNIQUE(incident_id, kind)
);
CREATE TABLE IF NOT EXISTS families (id uuid PRIMARY KEY, revoked boolean NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS tokens (
 digest text PRIMARY KEY, family_id uuid NOT NULL REFERENCES families(id),
 consumed boolean NOT NULL DEFAULT false, expires_at timestamptz NOT NULL DEFAULT now() + interval '1 day'
);
