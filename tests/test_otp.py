"""
tests/test_otp.py
=================
Unit tests for core.otp (OTP generation, hashing, verification, HMAC).
"""

from __future__ import annotations

import pytest

from core.otp import generate_otp, hash_otp, hmac_email, mask_email, verify_otp


class TestGenerateOtp:
    """Tests for generate_otp()."""

    @pytest.mark.parametrize("length", [4, 6, 8, 10])
    def test_correct_length(self, length: int) -> None:
        otp = generate_otp(length)
        assert len(otp) == length

    @pytest.mark.parametrize("length", [4, 6, 8, 10])
    def test_digits_only(self, length: int) -> None:
        otp = generate_otp(length)
        assert otp.isdigit(), f"Expected all digits, got: {otp!r}"

    def test_zero_padded(self) -> None:
        """Statistically, about 10% of 4-digit OTPs should start with 0."""
        found_zero_padded = False
        for _ in range(500):
            otp = generate_otp(4)
            if otp.startswith("0"):
                found_zero_padded = True
                assert len(otp) == 4  # Must still be 4 chars
                break
        # This test is probabilistic; 500 iterations with P(fail)=(0.9)^500 ≈ 0
        assert found_zero_padded, "Zero-padding never observed in 500 iterations"

    def test_randomness_no_duplicate_streak(self) -> None:
        """Generate 100 OTPs and ensure we do not get all identical values."""
        otps = {generate_otp(6) for _ in range(100)}
        assert len(otps) > 1, "Expected some variation in 100 OTPs"

    def test_length_too_small_raises(self) -> None:
        with pytest.raises(ValueError):
            generate_otp(0)

    def test_length_too_large_raises(self) -> None:
        with pytest.raises(ValueError):
            generate_otp(11)

    def test_default_length_is_six(self) -> None:
        otp = generate_otp()
        assert len(otp) == 6


class TestHashOtp:
    """Tests for hash_otp()."""

    def test_returns_string(self) -> None:
        result = hash_otp("123456")
        assert isinstance(result, str)

    def test_bcrypt_prefix(self) -> None:
        result = hash_otp("123456")
        assert result.startswith("$2b$"), f"Expected bcrypt hash, got: {result!r}"

    def test_different_hashes_for_same_otp(self) -> None:
        """bcrypt salting should produce different hashes each call."""
        h1 = hash_otp("123456")
        h2 = hash_otp("123456")
        assert h1 != h2, "bcrypt should produce different hashes (different salts)"


class TestVerifyOtp:
    """Tests for verify_otp()."""

    def test_correct_otp_returns_true(self) -> None:
        otp = "847291"
        hashed = hash_otp(otp)
        assert verify_otp(otp, hashed) is True

    def test_wrong_otp_returns_false(self) -> None:
        hashed = hash_otp("123456")
        assert verify_otp("000000", hashed) is False

    def test_empty_otp_returns_false(self) -> None:
        hashed = hash_otp("123456")
        assert verify_otp("", hashed) is False

    def test_malformed_hash_returns_false(self) -> None:
        assert verify_otp("123456", "not-a-valid-bcrypt-hash") is False

    @pytest.mark.parametrize("length", [4, 6, 8])
    def test_roundtrip_various_lengths(self, length: int) -> None:
        otp = generate_otp(length)
        hashed = hash_otp(otp)
        assert verify_otp(otp, hashed) is True


class TestHmacEmail:
    """Tests for hmac_email()."""

    def test_returns_hex_string(self) -> None:
        result = hmac_email("user@example.com")
        assert isinstance(result, str)
        bytes.fromhex(result)  # must be valid hex

    def test_64_chars_long(self) -> None:
        result = hmac_email("user@example.com")
        assert len(result) == 64  # SHA-256 → 32 bytes → 64 hex chars

    def test_deterministic(self) -> None:
        email = "user@example.com"
        assert hmac_email(email) == hmac_email(email)

    def test_case_insensitive(self) -> None:
        assert hmac_email("User@Example.COM") == hmac_email("user@example.com")

    def test_different_emails_different_digests(self) -> None:
        assert hmac_email("alice@example.com") != hmac_email("bob@example.com")

    def test_whitespace_trimmed(self) -> None:
        assert hmac_email("  user@example.com  ") == hmac_email("user@example.com")


class TestMaskEmail:
    """Tests for mask_email()."""

    def test_normal_email(self) -> None:
        result = mask_email("john.doe@example.com")
        assert result == "jo***@example.com"

    def test_short_local_part(self) -> None:
        result = mask_email("ab@x.io")
        assert result == "a***@x.io"

    def test_single_char_local(self) -> None:
        result = mask_email("a@b.com")
        assert result == "a***@b.com"

    def test_domain_is_preserved(self) -> None:
        result = mask_email("hello@gmail.com")
        assert result.endswith("@gmail.com")

    def test_invalid_email_fallback(self) -> None:
        result = mask_email("notanemail")
        assert result == "***@***"
