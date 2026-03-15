"""
tests/test_crypto.py
====================
Unit tests for core.crypto (AES-256-GCM encrypt/decrypt).
"""

from __future__ import annotations

import pytest

from core.crypto import decrypt, encrypt


class TestEncryptDecryptRoundTrip:
    """Verify that encrypt/decrypt are inverse operations."""

    def test_basic_roundtrip(self) -> None:
        plaintext = "Hello, MailGuard!"
        ciphertext = encrypt(plaintext)
        assert decrypt(ciphertext) == plaintext

    def test_roundtrip_unicode(self) -> None:
        plaintext = "密码123 🔑"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_roundtrip_long_string(self) -> None:
        plaintext = "x" * 10_000
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_roundtrip_special_chars(self) -> None:
        plaintext = "p@$$w0rd!#%^&*()-_=+[]{}|;':\",./<>?"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_different_ciphertexts_same_plaintext(self) -> None:
        """Each encrypt call must produce a unique ciphertext (fresh IV)."""
        pt = "same plaintext"
        ct1 = encrypt(pt)
        ct2 = encrypt(pt)
        assert ct1 != ct2, "Two encryptions of the same plaintext must differ"

    def test_ciphertext_is_hex_string(self) -> None:
        ct = encrypt("test")
        assert isinstance(ct, str)
        # Must be valid hex
        bytes.fromhex(ct)

    def test_ciphertext_minimum_length(self) -> None:
        # 12-byte IV (24 hex) + at least 1 byte ciphertext + 16-byte tag (32 hex)
        ct = encrypt("a")
        assert len(ct) >= 24 + 2 + 32  # iv + ciphertext + tag


class TestEncryptEdgeCases:
    """Edge-case and error-path tests for encrypt."""

    def test_empty_plaintext_raises(self) -> None:
        with pytest.raises(ValueError, match="plaintext must not be empty"):
            encrypt("")


class TestDecryptEdgeCases:
    """Edge-case and error-path tests for decrypt."""

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            decrypt("ab")

    def test_non_hex_raises(self) -> None:
        with pytest.raises(ValueError, match="non-hex"):
            decrypt("z" * 100)

    def test_tampered_ciphertext_raises(self) -> None:
        from cryptography.exceptions import InvalidTag  # type: ignore[import]

        ct = encrypt("sensitive data")
        # Flip last byte of the hex string to corrupt the authentication tag
        tampered = ct[:-2] + format((int(ct[-2:], 16) ^ 0xFF), "02x")
        with pytest.raises(InvalidTag):
            decrypt(tampered)

    def test_tampered_iv_raises(self) -> None:
        from cryptography.exceptions import InvalidTag  # type: ignore[import]

        ct = encrypt("sensitive data")
        # Flip the first byte of the IV
        tampered = format((int(ct[:2], 16) ^ 0xFF), "02x") + ct[2:]
        with pytest.raises(InvalidTag):
            decrypt(tampered)
