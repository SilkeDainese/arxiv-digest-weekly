"""Cloud Function: subscribe

POST /subscribe
Body: {"email": "student@example.com"}

Flow:
  1. Validate email format
  2. Generate HMAC verification token (purpose="verify", TTL=48h)
  3. Write Firestore doc at subscribers/{sha256(email)}:
       {email, created_at, verified: False, verify_token_hash, source}
  4. Send confirmation email via Gmail API
  5. Return {ok: true}

GDPR:
  - Doc ID is SHA-256(email) — no email in doc ID
  - verified:False docs never receive digest
  - Data stored in europe-west1 only
  - No rate limiting (Sprint 6 if needed)

CORS: allows silkedainese.github.io only.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone

import functions_framework

from shared.firestore_client import subscribers_col
from shared.gmail_client import build_message, send_message
from shared.secrets import get_hmac_secret
from shared.tokens import generate_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "silke-hub")
REGION = os.environ.get("FUNCTION_REGION", "europe-west1")
VERIFY_BASE_URL = (
    f"https://{REGION}-{PROJECT_ID}.cloudfunctions.net/verify"
)

# Simple email regex — rejects obviously broken addresses, not a full RFC 5322 parser
_EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,253}\.[^\s@]{2,}$")

# CORS origin
ALLOWED_ORIGIN = "https://silkedainese.github.io"

PURPOSE_VERIFY = "verify"


def _cors_headers(request_origin: str | None) -> dict:
    origin = ALLOWED_ORIGIN if request_origin == ALLOWED_ORIGIN else ALLOWED_ORIGIN
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "3600",
    }


def _error(msg: str, code: int, cors: dict):
    return ({"error": msg}, code, {**cors, "Content-Type": "application/json"})


def _ok(cors: dict):
    return ({"ok": True}, 200, {**cors, "Content-Type": "application/json"})


@functions_framework.http
def subscribe(request):
    """Handle signup form POST from silkedainese.github.io."""
    cors = _cors_headers(request.headers.get("Origin"))

    # ── Preflight ──────────────────────────────────────────────────────────
    if request.method == "OPTIONS":
        return ("", 204, cors)

    if request.method != "POST":
        return _error("Method not allowed", 405, cors)

    # ── Parse body ─────────────────────────────────────────────────────────
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        return _error("Invalid request body", 400, cors)

    email = (body.get("email") or "").strip().lower()

    # ── Validate email ─────────────────────────────────────────────────────
    if not email:
        return _error("Email is required", 400, cors)
    if len(email) > 254:
        return _error("Email address is too long", 400, cors)
    if not _EMAIL_RE.match(email):
        return _error("That email looks off — please check and try again.", 400, cors)

    # ── Check for existing doc (idempotent re-signup) ──────────────────────
    email_hash = hashlib.sha256(email.encode()).hexdigest()
    doc_ref = subscribers_col().document(email_hash)
    existing = doc_ref.get()

    if existing.exists:
        data = existing.to_dict()
        if data.get("verified"):
            # Already verified — silently return ok (don't leak whether subscribed)
            logger.info("Re-signup attempt for already-verified email hash %s", email_hash[:8])
            return _ok(cors)
        else:
            # Pending verification — resend the confirmation email
            logger.info("Resending confirmation for unverified email hash %s", email_hash[:8])
            try:
                _send_confirmation(email, email_hash)
            except Exception as exc:
                logger.error("ERR-ARXIV-SUBSCRIBE-EMAIL: Failed to resend confirmation: %s", exc)
                return _error("Could not send confirmation email — please try again.", 500, cors)
            return _ok(cors)

    # ── Generate verification token ────────────────────────────────────────
    try:
        hmac_secret = get_hmac_secret()
    except Exception as exc:
        logger.error("ERR-ARXIV-SUBSCRIBE-SECRET: Could not load hmac-secret: %s", exc)
        return _error("Server configuration error — please try again later.", 500, cors)

    verify_token = generate_token(email, PURPOSE_VERIFY, hmac_secret, ttl_override=48 * 3600)
    # Store hash of token (not the token itself) so Firestore dump doesn't expose valid tokens
    token_hash = hashlib.sha256(verify_token.encode()).hexdigest()

    # ── Write Firestore doc ────────────────────────────────────────────────
    try:
        doc_ref.set({
            "email": email,
            "created_at": datetime.now(timezone.utc),
            "verified": False,
            "verify_token_hash": token_hash,
            "source": "signup_v1",
        })
        logger.info("Subscriber doc created: hash=%s", email_hash[:8])
    except Exception as exc:
        logger.error("ERR-ARXIV-SUBSCRIBE-FIRESTORE: Write failed: %s", exc)
        return _error("Could not save your signup — please try again.", 500, cors)

    # ── Send confirmation email ────────────────────────────────────────────
    try:
        verify_url = f"{VERIFY_BASE_URL}?token={verify_token}"
        _send_confirmation_with_url(email, verify_url)
    except Exception as exc:
        logger.error("ERR-ARXIV-SUBSCRIBE-EMAIL: Gmail send failed: %s", exc)
        # Doc is written — subscriber can retry by re-submitting the form
        return _error("Could not send confirmation email — please try again.", 500, cors)

    return _ok(cors)


def _send_confirmation(email: str, email_hash: str) -> None:
    """Regenerate and resend a confirmation email for an unverified doc."""
    hmac_secret = get_hmac_secret()
    verify_token = generate_token(email, PURPOSE_VERIFY, hmac_secret, ttl_override=48 * 3600)
    # Update token hash on the doc
    token_hash = hashlib.sha256(verify_token.encode()).hexdigest()
    subscribers_col().document(email_hash).update({"verify_token_hash": token_hash})
    verify_url = f"{VERIFY_BASE_URL}?token={verify_token}"
    _send_confirmation_with_url(email, verify_url)


def _send_confirmation_with_url(email: str, verify_url: str) -> None:
    """Build and send the confirmation email."""
    subject = "Confirm your arXiv Digest subscription"
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"></head>
<body style="font-family:Georgia,serif;max-width:520px;margin:40px auto;padding:20px;color:#2D2D2D;line-height:1.6;">
  <h2 style="font-size:20px;color:#2C5530;margin-bottom:8px;">Almost there</h2>
  <p>Click the link below to confirm your subscription to the Weekly arXiv Digest:</p>
  <p style="margin:28px 0;">
    <a href="{verify_url}"
       style="display:inline-block;padding:12px 24px;background:#2C5530;color:#fff;
              text-decoration:none;border-radius:4px;font-size:15px;">
      Confirm subscription
    </a>
  </p>
  <p style="color:#666;font-size:14px;">
    This link expires in 48 hours. If you didn't sign up, you can ignore this email.
  </p>
  <hr style="border:none;border-top:1px solid #DDD;margin:32px 0 20px;">
  <p style="font-size:13px;color:#888;">
    — Silke Dainese, Aarhus University<br>
    <a href="mailto:silke.dainese@phys.au.dk" style="color:#888;">silke.dainese@phys.au.dk</a>
  </p>
</body>
</html>"""
    text_body = (
        f"Almost there — click the link below to confirm your arXiv Digest subscription:\n\n"
        f"{verify_url}\n\n"
        f"This link expires in 48 hours.\n"
        f"If you didn't sign up, you can ignore this email.\n\n"
        f"— Silke Dainese, Aarhus University"
    )
    msg = build_message(
        to_email=email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    send_message(msg)
