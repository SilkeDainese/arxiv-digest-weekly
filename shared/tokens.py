"""HMAC token generation and verification for all signed URLs.

Token format: base64url(payload_json).<signature>
where payload_json = {"e": email, "x": expiry_ts, "p": purpose}
and signature = hmac_sha256(secret, payload_bytes)

Purposes: "unsubscribe", "manage", "cancel_send"
Expiry: 90 days for unsubscribe/manage, 48 hours for cancel_send
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Literal

# Token purposes — never reuse across purposes (SEC principle: scope tokens)
PURPOSE_UNSUBSCRIBE = "unsubscribe"
PURPOSE_MANAGE = "manage"
PURPOSE_CANCEL_SEND = "cancel_send"

# Expiry in seconds
TTL_LONG = 90 * 24 * 3600    # 90 days: unsubscribe + manage
TTL_SHORT = 48 * 3600         # 48 hours: cancel_send


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    # Re-pad to multiple of 4
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def generate_token(
    email: str,
    purpose: Literal["unsubscribe", "manage", "cancel_send"],
    secret: str,
    *,
    week_iso: str | None = None,
    ttl_override: int | None = None,
) -> str:
    """Generate a signed token for the given email + purpose.

    Args:
        email: Subscriber email address.
        purpose: One of PURPOSE_* constants — tokens are purpose-scoped.
        secret: HMAC secret (32-byte string from Secret Manager).
        week_iso: Required when purpose == "cancel_send" (e.g. "2026-W15").
        ttl_override: Optional expiry in seconds (overrides defaults).

    Returns:
        URL-safe token string.

    Raises:
        ValueError: If week_iso is missing for cancel_send.
    """
    if purpose == PURPOSE_CANCEL_SEND and not week_iso:
        raise ValueError("week_iso is required for cancel_send tokens")

    ttl = ttl_override if ttl_override is not None else (
        TTL_SHORT if purpose == PURPOSE_CANCEL_SEND else TTL_LONG
    )
    expiry_ts = int(time.time()) + ttl

    payload: dict = {
        "e": email,
        "x": expiry_ts,
        "p": purpose,
    }
    if week_iso:
        payload["w"] = week_iso

    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)

    sig = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).digest()
    sig_b64 = _b64url_encode(sig)

    return f"{payload_b64}.{sig_b64}"


def verify_token(
    token: str,
    expected_purpose: str,
    secret: str,
    *,
    now: float | None = None,
) -> dict:
    """Verify a signed token and return its payload.

    Args:
        token: Token string from URL parameter.
        expected_purpose: The purpose this endpoint serves.
        secret: HMAC secret from Secret Manager.
        now: Override current time (for testing only).

    Returns:
        Payload dict with keys: e (email), x (expiry), p (purpose), w? (week_iso).

    Raises:
        TokenInvalidError: If signature is wrong, token is malformed, or purpose mismatch.
        TokenExpiredError: If the token has expired.
    """
    now_ts = now if now is not None else time.time()

    try:
        payload_b64, sig_b64 = token.rsplit(".", 1)
    except ValueError:
        raise TokenInvalidError("malformed token: missing separator")

    try:
        payload_bytes = _b64url_decode(payload_b64)
        expected_sig = _b64url_decode(sig_b64)
    except Exception:
        raise TokenInvalidError("malformed token: base64 decode failed")

    # Constant-time comparison (prevents timing attacks)
    actual_sig = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).digest()

    if not hmac.compare_digest(actual_sig, expected_sig):
        raise TokenInvalidError("invalid token signature")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise TokenInvalidError("malformed token: payload decode failed")

    if payload.get("p") != expected_purpose:
        raise TokenInvalidError(
            f"wrong purpose: got '{payload.get('p')}', expected '{expected_purpose}'"
        )

    if payload.get("x", 0) < now_ts:
        raise TokenExpiredError("token has expired")

    return payload


class TokenInvalidError(Exception):
    """Raised when a token's signature is wrong or the token is malformed."""


class TokenExpiredError(Exception):
    """Raised when a token was valid but has expired."""
