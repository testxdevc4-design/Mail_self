-- 006_create_bot_sessions.sql
-- Stores Telegram bot conversation state (wizard in-progress data).

CREATE TABLE IF NOT EXISTS bot_sessions (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      BIGINT      NOT NULL UNIQUE,
    session_data JSONB       NOT NULL DEFAULT '{}',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bot_sessions_user ON bot_sessions(user_id);

COMMENT ON TABLE  bot_sessions IS 'Persistent Telegram bot conversation state for admin wizards.';
COMMENT ON COLUMN bot_sessions.user_id      IS 'Telegram user ID (bigint to match the Telegram API type).';
COMMENT ON COLUMN bot_sessions.session_data IS 'JSONB blob containing the current wizard step and collected values.';
