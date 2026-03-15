-- 001_create_sender_emails.sql
-- Stores SMTP sender accounts.  App passwords are AES-256-GCM encrypted
-- before insertion (never stored in plaintext).

CREATE TABLE IF NOT EXISTS sender_emails (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    email_address    VARCHAR(255) NOT NULL UNIQUE,
    display_name     VARCHAR(100),
    provider         VARCHAR(50)  NOT NULL,
    smtp_host        VARCHAR(255) NOT NULL,
    smtp_port        SMALLINT     NOT NULL,
    app_password_enc TEXT         NOT NULL,
    daily_limit      INTEGER      NOT NULL DEFAULT 500,
    is_verified      BOOLEAN      NOT NULL DEFAULT false,
    is_active        BOOLEAN      NOT NULL DEFAULT true,
    last_used_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  sender_emails IS 'SMTP sender accounts used to dispatch OTP emails.';
COMMENT ON COLUMN sender_emails.app_password_enc IS 'AES-256-GCM encrypted SMTP app password (hex: iv + ciphertext + tag).';
COMMENT ON COLUMN sender_emails.is_verified IS 'Set to true after a successful test send.';
