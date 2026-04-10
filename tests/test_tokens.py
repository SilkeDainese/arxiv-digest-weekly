"""Tests for HMAC token generation and verification.

Covers:
  - generate + verify (positive case)
  - tampered token rejection
  - expired token rejection
  - wrong-purpose rejection
  - cancel_send requires week_iso
  - token format (two segments separated by '.')
"""
import pytest
import time

from shared.tokens import (
    PURPOSE_CANCEL_SEND,
    PURPOSE_MANAGE,
    PURPOSE_UNSUBSCRIBE,
    TokenExpiredError,
    TokenInvalidError,
    generate_token,
    verify_token,
)

SECRET = "test-secret-32-bytes-padded-ok!!"
EMAIL = "student@phys.au.dk"


class TestGenerateVerify:
    def test_unsubscribe_roundtrip(self):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        payload = verify_token(token, PURPOSE_UNSUBSCRIBE, SECRET)
        assert payload["e"] == EMAIL
        assert payload["p"] == PURPOSE_UNSUBSCRIBE
        assert payload["x"] > time.time()

    def test_manage_roundtrip(self):
        token = generate_token(EMAIL, PURPOSE_MANAGE, SECRET)
        payload = verify_token(token, PURPOSE_MANAGE, SECRET)
        assert payload["e"] == EMAIL
        assert payload["p"] == PURPOSE_MANAGE

    def test_cancel_send_roundtrip(self):
        token = generate_token(EMAIL, PURPOSE_CANCEL_SEND, SECRET, week_iso="2026-W15")
        payload = verify_token(token, PURPOSE_CANCEL_SEND, SECRET)
        assert payload["e"] == EMAIL
        assert payload["p"] == PURPOSE_CANCEL_SEND
        assert payload["w"] == "2026-W15"

    def test_token_has_two_segments(self):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        parts = token.split(".")
        assert len(parts) == 2, "Token must be payload.signature"

    def test_cancel_send_missing_week_raises(self):
        with pytest.raises(ValueError, match="week_iso is required"):
            generate_token(EMAIL, PURPOSE_CANCEL_SEND, SECRET)


class TestTamperedToken:
    def test_modified_payload_rejected(self):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        payload_b64, sig_b64 = token.split(".")
        # Flip a character in the payload
        tampered_payload = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
        tampered_token = f"{tampered_payload}.{sig_b64}"
        with pytest.raises(TokenInvalidError):
            verify_token(tampered_token, PURPOSE_UNSUBSCRIBE, SECRET)

    def test_modified_signature_rejected(self):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        payload_b64, sig_b64 = token.split(".")
        # Replace the entire signature with zeroed-out bytes encoded as base64
        import base64, hashlib
        zero_sig = base64.urlsafe_b64encode(b"\x00" * 32).rstrip(b"=").decode("ascii")
        tampered_token = f"{payload_b64}.{zero_sig}"
        with pytest.raises(TokenInvalidError):
            verify_token(tampered_token, PURPOSE_UNSUBSCRIBE, SECRET)

    def test_wrong_secret_rejected(self):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        with pytest.raises(TokenInvalidError):
            verify_token(token, PURPOSE_UNSUBSCRIBE, "wrong-secret-here!!!!!!!!!!!!!!")

    def test_missing_separator_rejected(self):
        with pytest.raises(TokenInvalidError, match="malformed token: missing separator"):
            verify_token("notavalidtoken", PURPOSE_UNSUBSCRIBE, SECRET)

    def test_garbage_token_rejected(self):
        with pytest.raises(TokenInvalidError):
            verify_token("garbage.garbage", PURPOSE_UNSUBSCRIBE, SECRET)


class TestExpiredToken:
    def test_expired_token_raises(self):
        # TTL = -1 second means it's already expired
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET, ttl_override=-1)
        with pytest.raises(TokenExpiredError):
            verify_token(token, PURPOSE_UNSUBSCRIBE, SECRET)

    def test_not_yet_expired_passes(self):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET, ttl_override=3600)
        payload = verify_token(token, PURPOSE_UNSUBSCRIBE, SECRET)
        assert payload["e"] == EMAIL

    def test_expired_via_now_override(self):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET, ttl_override=3600)
        # "now" is far in the future
        with pytest.raises(TokenExpiredError):
            verify_token(token, PURPOSE_UNSUBSCRIBE, SECRET, now=time.time() + 10_000)


class TestWrongPurpose:
    def test_unsubscribe_token_rejected_by_manage_endpoint(self):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        with pytest.raises(TokenInvalidError, match="wrong purpose"):
            verify_token(token, PURPOSE_MANAGE, SECRET)

    def test_manage_token_rejected_by_cancel_endpoint(self):
        token = generate_token(EMAIL, PURPOSE_MANAGE, SECRET)
        with pytest.raises(TokenInvalidError, match="wrong purpose"):
            verify_token(token, PURPOSE_CANCEL_SEND, SECRET)

    def test_cancel_token_rejected_by_unsubscribe_endpoint(self):
        token = generate_token(EMAIL, PURPOSE_CANCEL_SEND, SECRET, week_iso="2026-W15")
        with pytest.raises(TokenInvalidError, match="wrong purpose"):
            verify_token(token, PURPOSE_UNSUBSCRIBE, SECRET)
