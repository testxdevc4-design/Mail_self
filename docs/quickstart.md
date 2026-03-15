# MailGuard Quick-Start Guide

Get a fully functional OTP service running in under 15 minutes.

---

## Prerequisites

- A [Supabase](https://supabase.com) account (free tier works)
- An [Upstash](https://upstash.com) Redis database (free tier works)
- A [Railway](https://railway.app) account for deployment
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A Gmail address with an [App Password](./gmail-setup.md)

---

## Step 1 – Apply the Database Migrations

1. Open your Supabase project → **SQL Editor**.
2. Run each migration file in order:

```sql
-- Paste and run these files one by one:
-- db/migrations/001_create_sender_emails.sql
-- db/migrations/002_create_projects.sql
-- db/migrations/003_create_api_keys.sql
-- db/migrations/004_create_otp_records.sql
-- db/migrations/005_create_email_logs.sql
-- db/migrations/006_create_bot_sessions.sql
```

3. Verify by checking **Table Editor** – you should see all 6 tables.

---

## Step 2 – Configure Upstash Redis

1. Go to [console.upstash.com](https://console.upstash.com) and create a
   **Redis** database (select the region closest to your Railway deployment).
2. Copy the **TLS connection string** that starts with `rediss://`.
3. You'll use this as the `REDIS_URL` environment variable.

---

## Step 3 – Generate Secrets

Run these commands locally to generate your secrets:

```bash
# AES-256 encryption key (exactly 64 hex chars)
python -c "import secrets; print(secrets.token_hex(32))"

# JWT signing secret (at least 64 chars)
python -c "import secrets; print(secrets.token_hex(64))"
```

---

## Step 4 – Deploy to Railway

1. Fork this repository.
2. In Railway, create a new project → **Deploy from GitHub repo**.
3. Select your fork.
4. Railway auto-detects the `railway.toml` and creates 3 services:
   `api`, `worker`, `bot`.
5. Set the following environment variables on **all three** services:

| Variable | Value |
|----------|-------|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Your Supabase service role key |
| `REDIS_URL` | Your Upstash TLS URL (`rediss://...`) |
| `ENCRYPTION_KEY` | 64-char hex string (from step 3) |
| `JWT_SECRET` | 64+ char string (from step 3) |
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `TELEGRAM_ADMIN_UID` | Your Telegram numeric user ID |
| `ENV` | `production` |

6. Deploy.  The `api` service exposes `GET /health` for a liveness check.

---

## Step 5 – Configure via the Telegram Bot

Open Telegram and message your bot.

```
/addemail       → Add a Gmail sender (wizard)
/newproject     → Create a project (wizard)
/genkey myapp   → Generate a live API key
```

The bot will guide you through each step.

---

## Step 6 – Make Your First API Call

```bash
# Send an OTP
curl -X POST https://your-api.railway.app/api/v1/otp/send \
  -H "Authorization: Bearer mg_live_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "purpose": "verification"}'

# Verify the OTP
curl -X POST https://your-api.railway.app/api/v1/otp/verify \
  -H "Authorization: Bearer mg_live_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "code": "123456"}'
```

A successful verify returns a short-lived JWT you can use to gate access in
your application.

---

## Local Development

```bash
# 1. Clone and install
git clone https://github.com/YOUR_ORG/mailguard.git
cd mailguard
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Copy and fill in the env file
cp .env.example .env

# 3. Start Redis locally
docker-compose up redis -d

# 4. Run the API
uvicorn apps.api.main:app --reload --port 3000

# 5. Run the worker (separate terminal)
python -m arq apps.worker.main.WorkerSettings

# 6. Run the bot (separate terminal)
python apps/bot/main.py
```

---

## Running Tests

```bash
pytest -v
```
