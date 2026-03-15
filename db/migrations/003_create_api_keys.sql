-- 003_create_api_keys.sql
-- API keys are stored as SHA-256 hashes only; the plaintext is shown once.

CREATE TABLE IF NOT EXISTS api_keys (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID         NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key_hash     TEXT         NOT NULL UNIQUE,
    key_prefix   VARCHAR(20)  NOT NULL,
    label        VARCHAR(100),
    is_sandbox   BOOLEAN      NOT NULL DEFAULT false,
    is_active    BOOLEAN      NOT NULL DEFAULT true,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_project ON api_keys(project_id);

COMMENT ON TABLE  api_keys IS 'API keys for authenticating OTP API requests.';
COMMENT ON COLUMN api_keys.key_hash   IS 'SHA-256 hex digest of the plaintext key.  Plaintext is never stored.';
COMMENT ON COLUMN api_keys.key_prefix IS 'First ~12 characters of the plaintext key for human identification (e.g. mg_live_ab12).';
COMMENT ON COLUMN api_keys.is_sandbox IS 'Sandbox keys start with mg_test_ and are blocked in production.';
