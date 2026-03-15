# Gmail App Password Setup for MailGuard

MailGuard uses SMTP to send OTP emails.  If you use Gmail as your sender,
you **must** create an App Password — Gmail blocks sign-in via plain SMTP
passwords for accounts with 2-Step Verification enabled (which is required).

---

## Step 1 – Enable 2-Step Verification

1. Go to [myaccount.google.com](https://myaccount.google.com).
2. Click **Security** in the left sidebar.
3. Under *How you sign in to Google*, click **2-Step Verification**.
4. Follow the prompts to enable it.

---

## Step 2 – Generate an App Password

1. Return to [myaccount.google.com → Security](https://myaccount.google.com/security).
2. Under *How you sign in to Google*, click **App passwords**
   (this option only appears once 2-Step Verification is on).
3. At the bottom, type a name for the app (e.g. `MailGuard`) and click
   **Create**.
4. Google shows a 16-character password.  **Copy it immediately** – it
   will never be shown again.

---

## Step 3 – Add the Sender via the Bot

Open the Telegram admin bot and run:

```
/addemail
```

Follow the wizard:

| Step | What to enter |
|------|---------------|
| Email | `youraddress@gmail.com` |
| App password | The 16-char password from step 2 (no spaces) |
| Provider | `gmail` |

The bot will:
1. Encrypt the app password with AES-256-GCM.
2. Save the sender to the database.
3. Send a test email to confirm the credentials.

---

## SMTP settings (auto-filled for Gmail)

| Setting | Value |
|---------|-------|
| SMTP host | `smtp.gmail.com` |
| SMTP port | `587` (STARTTLS) |
| Auth | OAuth2 / App password |

---

## Troubleshooting

| Error | Likely cause | Fix |
|-------|-------------|-----|
| `535-5.7.8 Username and Password not accepted` | Wrong app password or plain password used | Generate a new app password |
| `SMTPConnectError` | Port blocked | Ensure port 587 is open outbound |
| `SMTPAuthenticationError` | 2FA not enabled | Enable 2-Step Verification first |

---

## Other Providers

| Provider | SMTP host | Port |
|----------|-----------|------|
| Outlook / Office 365 | `smtp.office365.com` | 587 |
| Zoho | `smtp.zoho.com` | 587 |
| Custom | Your SMTP server | Usually 587 or 465 |

When running `/addemail`, select **other** for custom providers and enter
your SMTP host and port manually.
