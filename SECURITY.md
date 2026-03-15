# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest (`main`) | ✅ |
| All older tags | ❌ |

We only maintain the latest version of MailGuard.  Please upgrade before
reporting a vulnerability.

---

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

To report a security issue privately:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Fill in the form with as much detail as possible.

Alternatively, email: **security@ideanax.com**

We aim to respond within **72 hours** and provide a fix or mitigation
within **14 days** for critical issues.

---

## Scope

The following are in scope for security reports:

- Authentication or authorisation bypass in the API or bot.
- Cryptographic weaknesses (AES key handling, bcrypt bypass, JWT forgery).
- SQL / NoSQL injection.
- Remote code execution.
- Leakage of PII (email addresses) through logs or API responses.
- Rate-limit bypass techniques.
- SMTP credential exposure.

The following are **out of scope**:

- Issues in third-party dependencies (report those upstream).
- Denial-of-service attacks requiring exceptional resources.
- Social engineering.
- Phishing unrelated to MailGuard.

---

## Security Architecture

MailGuard is designed with defence-in-depth:

### Data Protection

| Data | Storage method |
|------|---------------|
| Recipient emails | HMAC-SHA256 (non-reversible, keyed) |
| OTP codes | bcrypt hash (cost 10) – never stored plain |
| SMTP passwords | AES-256-GCM encrypted – never stored plain |
| API keys | SHA-256 hash – plaintext shown once, never stored |
| JWT secrets | Environment variables only |

### Network Security

- All API responses include security headers via the `secure` library
  (Strict-Transport-Security, X-Content-Type-Options, X-Frame-Options, etc.).
- CORS is configurable via `ALLOWED_ORIGINS`.
- HTTPS is enforced at the Railway / reverse-proxy layer.

### Anti-Enumeration

- OTP endpoints enforce a **minimum 200 ms response time** regardless of
  whether the email exists, preventing timing-based user enumeration.

### Rate Limiting

Five-tier sliding-window rate limits prevent brute-force and abuse:
- Global IP (60/min)
- Project + IP (30/min)
- Project + email (5/hour)
- Project global (1 000/hour)
- Sandbox per-project (20/hour)

### OTP Security

- Generated with `secrets.randbelow` (CSPRNG) – never `random`.
- bcrypt cost factor 10 (OWASP recommended minimum).
- Attempt counting with automatic lock after `otp_max_attempts`.
- Expiry enforced server-side – OTPs cannot be used after expiry.
- Verified OTPs are immediately invalidated.
- Previous pending OTPs are invalidated on each new send.

### Sandbox Key Blocking

API keys prefixed with `mg_test_` are blocked in `ENV=production`,
preventing test credentials from reaching production systems.

---

## Dependency Management

Dependencies are pinned to exact versions in `requirements.txt`.
We recommend running `pip-audit` regularly to check for advisories:

```bash
pip install pip-audit
pip-audit -r requirements.txt
```

---

## Acknowledgements

We thank the security researchers who responsibly disclose vulnerabilities.
Reporters of valid critical/high issues will be credited in release notes
(with their permission).
