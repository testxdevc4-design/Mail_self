-- 004_create_otp_records.sql
-- Stores hashed OTPs.  Plaintext OTPs are never persisted.

CREATE TABLE IF NOT EXISTS otp_records (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID        NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    email_hash      TEXT        NOT NULL,
    otp_hash        TEXT        NOT NULL,
    purpose         VARCHAR(50),
    attempt_count   SMALLINT    NOT NULL DEFAULT 0,
    is_verified     BOOLEAN     NOT NULL DEFAULT false,
    is_invalidated  BOOLEAN     NOT NULL DEFAULT false,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_otp_records_email
    ON otp_records(email_hash, project_id);

CREATE INDEX IF NOT EXISTS idx_otp_records_expires
    ON otp_records(expires_at)
    WHERE is_verified = false AND is_invalidated = false;

COMMENT ON TABLE  otp_records IS 'One row per OTP generation event.  Expired rows can be pruned safely.';
COMMENT ON COLUMN otp_records.email_hash IS 'HMAC-SHA256 of the lowercase recipient email.  PII never stored in plaintext.';
COMMENT ON COLUMN otp_records.otp_hash   IS 'bcrypt hash (cost 10) of the plaintext OTP code.';
