-- 005_create_email_logs.sql
-- Audit trail for every email delivery attempt.

CREATE TABLE IF NOT EXISTS email_logs (
    id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id            UUID         REFERENCES projects(id)       ON DELETE SET NULL,
    sender_email_id       UUID         REFERENCES sender_emails(id)  ON DELETE SET NULL,
    recipient_email_hash  TEXT         NOT NULL,
    purpose               VARCHAR(50),
    status                VARCHAR(20)  NOT NULL DEFAULT 'pending',
    error_message         TEXT,
    attempt_count         SMALLINT     NOT NULL DEFAULT 1,
    sent_at               TIMESTAMPTZ,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_logs_project
    ON email_logs(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_email_logs_status
    ON email_logs(status);

CREATE INDEX IF NOT EXISTS idx_email_logs_created
    ON email_logs(created_at DESC);

COMMENT ON TABLE  email_logs IS 'Delivery audit log for every OTP email dispatch attempt.';
COMMENT ON COLUMN email_logs.status IS 'One of: pending, sent, failed, retrying.';
COMMENT ON COLUMN email_logs.recipient_email_hash IS 'HMAC-SHA256 of the lowercase recipient email.  PII never stored.';
