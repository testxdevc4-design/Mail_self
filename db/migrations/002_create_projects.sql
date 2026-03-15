-- 002_create_projects.sql
-- A project represents one application / use-case that consumes the OTP API.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'otp_format') THEN
        CREATE TYPE otp_format AS ENUM ('text', 'html');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS projects (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  VARCHAR(100) NOT NULL,
    slug                  VARCHAR(100) NOT NULL UNIQUE,
    sender_email_id       UUID        REFERENCES sender_emails(id) ON DELETE SET NULL,
    otp_length            SMALLINT    NOT NULL DEFAULT 6,
    otp_expiry_seconds    INTEGER     NOT NULL DEFAULT 600,
    otp_max_attempts      SMALLINT    NOT NULL DEFAULT 5,
    otp_subject_tmpl      TEXT,
    otp_body_tmpl         TEXT,
    otp_format            otp_format  NOT NULL DEFAULT 'text',
    rate_limit_per_hour   INTEGER     NOT NULL DEFAULT 10,
    is_active             BOOLEAN     NOT NULL DEFAULT true,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  projects IS 'Each project maps to one consumer application.';
COMMENT ON COLUMN projects.slug IS 'URL-safe unique identifier used in bot commands and logs.';
COMMENT ON COLUMN projects.otp_subject_tmpl IS 'Jinja2 template for the email subject.  NULL = use the built-in default.';
COMMENT ON COLUMN projects.otp_body_tmpl    IS 'Jinja2 template for the email body.  NULL = use the built-in default.';
