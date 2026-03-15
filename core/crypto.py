"""
core/crypto.py
==============
AES-256-GCM authenticated encryption helpers.

Each call to ``encrypt`` produces a fresh 12-byte random IV, so encrypting
the same plaintext twice yields different ciphertext – this is intentional.

Wire format (hex-encoded):
    <12-byte IV (24 hex chars)><ciphertext + 16-byte auth-tag (variable len)>

The authentication tag is appended by ``cryptography`` automatically when
using AESGCM, so the decrypt side does not need any special handling beyond
splitting the IV off the front.
"""

from __future__ import annotations

import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.config import settings

# ── Constants ─────────────────────────────────────────────────────────────────
_IV_BYTES: int = 12  # 96-bit IV recommended for GCM
_IV_HEX_LEN: int = _IV_BYTES * 2  # 24 hex characters


def encrypt(plaintext: str) -> str:
    """
    Encrypt *plaintext* with AES-256-GCM and return a hex-encoded string.

    The returned string contains the IV prepended to the ciphertext+tag so
    that ``decrypt`` receives everything it needs in one argument.

    Args:
        plaintext: The UTF-8 string to encrypt.

    Returns:
        Hex string: ``iv_hex + ciphertext_hex`` (no separator).

    Raises:
        ValueError: If *plaintext* is empty.
    """
    if not plaintext:
        raise ValueError("plaintext must not be empty")

    key: bytes = settings.encryption_key_bytes  # 32 bytes
    iv: bytes = os.urandom(_IV_BYTES)

    aesgcm = AESGCM(key)
    # AESGCM.encrypt returns ciphertext || tag (tag appended automatically)
    ciphertext_with_tag: bytes = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)

    return binascii.hexlify(iv + ciphertext_with_tag).decode("ascii")


def decrypt(ciphertext_hex: str) -> str:
    """
    Decrypt a hex string produced by :func:`encrypt`.

    Args:
        ciphertext_hex: The hex string returned by ``encrypt``.

    Returns:
        The original UTF-8 plaintext.

    Raises:
        ValueError: If the hex string is malformed or too short.
        cryptography.exceptions.InvalidTag: If authentication fails
            (i.e. the ciphertext or IV was tampered with).
    """
    if len(ciphertext_hex) < _IV_HEX_LEN + 2:  # at least 1 byte of ciphertext
        raise ValueError(
            f"ciphertext_hex is too short: expected at least {_IV_HEX_LEN + 2} "
            f"hex characters, got {len(ciphertext_hex)}"
        )

    try:
        raw: bytes = binascii.unhexlify(ciphertext_hex)
    except binascii.Error as exc:
        raise ValueError(f"ciphertext_hex contains non-hex characters: {exc}") from exc

    iv: bytes = raw[:_IV_BYTES]
    ciphertext_with_tag: bytes = raw[_IV_BYTES:]

    key: bytes = settings.encryption_key_bytes
    aesgcm = AESGCM(key)

    # Raises cryptography.exceptions.InvalidTag if integrity check fails
    plaintext_bytes: bytes = aesgcm.decrypt(iv, ciphertext_with_tag, None)
    return plaintext_bytes.decode("utf-8")
