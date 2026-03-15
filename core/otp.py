"""
core/otp.py
===========
OTP generation, hashing and verification utilities.

Security design decisions
--------------------------
* ``secrets.randbelow`` is a CSPRNG – never use ``random`` for OTPs.
* bcrypt cost factor 10 is the OWASP recommended minimum for password hashing;
  we re-use the same approach for OTP hashes so a leaked DB row cannot be
  brute-forced efficiently.
* HMAC-SHA256 is used to store the recipient email in a non-reversible but
  deterministic form so we can look up records without storing PII in plain
  text.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import bcrypt

from core.config import settings


# ── Constants ─────────────────────────────────────────────────────────────────
_BCRYPT_ROUNDS: int = 10
_MAX_OTP_LENGTH: int = 10


# ── Public API ────────────────────────────────────────────────────────────────


def generate_otp(length: int = 6) -> str:
    """
    Generate a cryptographically random numeric OTP of *length* digits.

    Args:
        length: Number of digits.  Must be between 1 and 10 inclusive.

    Returns:
        Zero-padded string of exactly *length* digits, e.g. ``"042891"``.

    Raises:
        ValueError: If *length* is outside the permitted range.

    Example::

        >>> otp = generate_otp(6)
        >>> len(otp) == 6
        True
        >>> otp.isdigit()
        True
    """
    if not (1 <= length <= _MAX_OTP_LENGTH):
        raise ValueError(
            f"OTP length must be between 1 and {_MAX_OTP_LENGTH}, got {length}"
        )
    upper_bound: int = 10 ** length
    value: int = secrets.randbelow(upper_bound)
    # Zero-pad to ensure constant width
    return str(value).zfill(length)


def hash_otp(otp: str) -> str:
    """
    Hash *otp* with bcrypt (cost factor 10) and return the hash as a string.

    Args:
        otp: The plain-text OTP produced by :func:`generate_otp`.

    Returns:
        bcrypt hash string suitable for storage.
    """
    hashed: bytes = bcrypt.hashpw(otp.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_otp(otp: str, hashed: str) -> bool:
    """
    Constant-time verification of *otp* against a bcrypt *hashed* value.

    Args:
        otp:    Plain-text OTP supplied by the end-user.
        hashed: Value previously returned by :func:`hash_otp`.

    Returns:
        ``True`` if the OTP matches, ``False`` otherwise.
    """
    try:
        return bcrypt.checkpw(otp.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:  # noqa: BLE001 – bcrypt can raise on malformed hashes
        return False


def hmac_email(email: str) -> str:
    """
    Produce a deterministic HMAC-SHA256 hex digest of *email*.

    We use the first 32 bytes of the AES encryption key as the HMAC key so
    that no additional secret is required.  The digest is stored in the DB
    instead of the raw address, protecting PII while still allowing exact-
    match lookups.

    Args:
        email: The recipient e-mail address (lowercased before hashing).

    Returns:
        Lowercase hex string (64 characters).
    """
    key_bytes: bytes = settings.encryption_key_bytes  # 32 bytes
    normalised: str = email.strip().lower()
    digest: str = hmac.new(
        key_bytes,
        normalised.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def mask_email(email: str) -> str:
    """
    Return a privacy-preserving masked representation of *email*.

    The local part is partially obscured so the API response gives the user
    enough context to recognise the destination without exposing the full
    address.

    Examples::

        >>> mask_email("john.doe@example.com")
        'jo***@example.com'
        >>> mask_email("ab@x.io")
        'a***@x.io'
        >>> mask_email("a@b.com")
        'a***@b.com'

    Args:
        email: Full e-mail address.

    Returns:
        Masked string such as ``"jo***@example.com"``.
    """
    try:
        local, domain = email.rsplit("@", 1)
    except ValueError:
        # Not a valid e-mail shape – return a fully redacted string
        return "***@***"

    visible: int = max(1, min(2, len(local) - 1))
    return local[:visible] + "***@" + domain
