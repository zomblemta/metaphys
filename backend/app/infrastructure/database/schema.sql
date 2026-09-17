CREATE TABLE IF NOT EXISTS app_schema (version integer PRIMARY KEY);
INSERT INTO app_schema VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS app_users (
 id text PRIMARY KEY, password_hash text NOT NULL, enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS app_sessions (
 token_hash text PRIMARY KEY, owner_id text NOT NULL REFERENCES app_users(id), csrf text NOT NULL,
 expires_at timestamptz NOT NULL DEFAULT now() + interval '24 hours'
);
CREATE TABLE IF NOT EXISTS app_conversations (
 id uuid PRIMARY KEY, owner_id text NOT NULL REFERENCES app_users(id), title text NOT NULL,
 failed boolean NOT NULL DEFAULT false, deleting boolean NOT NULL DEFAULT false,
 result jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_conversations_owner ON app_conversations(owner_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS app_runs (
 id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES app_conversations(id) ON DELETE CASCADE,
 input text NOT NULL, status text NOT NULL CHECK(status IN ('queued','running','succeeded','failed','timed_out','cancelled')),
 idempotency_key text, result jsonb, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(conversation_id, idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS app_runs_active ON app_runs(conversation_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS app_messages (
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 conversation_id uuid NOT NULL REFERENCES app_conversations(id) ON DELETE CASCADE,
 run_id uuid NOT NULL REFERENCES app_runs(id) ON DELETE CASCADE,
 role text NOT NULL, content text NOT NULL, UNIQUE(run_id, role)
);
CREATE TABLE IF NOT EXISTS app_events (
 run_id uuid NOT NULL REFERENCES app_runs(id) ON DELETE CASCADE,
 seq integer NOT NULL, kind text NOT NULL, data jsonb NOT NULL,
 PRIMARY KEY(run_id, seq)
);

CREATE TABLE IF NOT EXISTS app_artifacts (
 storage_key text PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS app_artifact_refs (
 storage_key text NOT NULL REFERENCES app_artifacts(storage_key),
 run_id uuid NOT NULL REFERENCES app_runs(id) ON DELETE CASCADE,
 PRIMARY KEY(storage_key, run_id)
);

CREATE TABLE IF NOT EXISTS app_daily_usage (
 owner_id text NOT NULL REFERENCES app_users(id), day date NOT NULL, count integer NOT NULL,
 PRIMARY KEY(owner_id,day)
);
