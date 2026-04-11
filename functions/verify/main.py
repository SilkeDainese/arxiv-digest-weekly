"""Cloud Function: verify

GET /verify?token=<hmac>

Validates the HMAC verification token, marks the subscriber as verified,
and returns an HTML confirmation page.

Token purpose: "verify", TTL 48h (set by subscribe function).
"""
from __future__ import annotations

import hashlib
import logging
import os

import functions_framework

from shared.firestore_client import subscribers_col
from shared.secrets import get_hmac_secret
from shared.tokens import TokenExpiredError, TokenInvalidError, verify_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "silke-hub")
SIGNUP_URL = "https://silkedainese.github.io/arxiv-digest/"

PURPOSE_VERIFY = "verify"


def _html_page(title: str, heading: str, body: str) -> tuple:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{
      font-family: Georgia, serif;
      max-width: 480px;
      margin: 80px auto;
      padding: 20px;
      color: #2D2D2D;
      line-height: 1.6;
      text-align: center;
    }}
    h1 {{ font-size: 22px; color: #2C5530; margin-bottom: 12px; }}
    p {{ margin: 0 0 16px; }}
    a {{ color: #2C5530; }}
    .muted {{ font-size: 14px; color: #888; }}
  </style>
</head>
<body>
  <h1>{heading}</h1>
  {body}
</body>
</html>"""
    return (html, 200, {"Content-Type": "text/html; charset=utf-8"})


def _error_page(message: str, code: int) -> tuple:
    body = f"""<p>{message}</p>
<p class="muted">
  <a href="{SIGNUP_URL}">Back to signup</a> to request a new link.
</p>"""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Confirmation failed</title>
  <style>
    body {{
      font-family: Georgia, serif;
      max-width: 480px;
      margin: 80px auto;
      padding: 20px;
      color: #2D2D2D;
      line-height: 1.6;
      text-align: center;
    }}
    h1 {{ font-size: 22px; color: #2D2D2D; margin-bottom: 12px; }}
    p {{ margin: 0 0 16px; }}
    a {{ color: #2C5530; }}
    .muted {{ font-size: 14px; color: #888; }}
  </style>
</head>
<body>
  <h1>Confirmation failed</h1>
  <p>{message}</p>
  <p class="muted">
    <a href="{SIGNUP_URL}">Sign up again</a> to get a fresh link.
  </p>
</body>
</html>"""
    return (html, code, {"Content-Type": "text/html; charset=utf-8"})


@functions_framework.http
def verify(request):
    """Handle verification link click."""
    token_str = request.args.get("token", "").strip()

    if not token_str:
        return _error_page("No token found in this link.", 400)

    # ── Validate token ─────────────────────────────────────────────────────
    try:
        hmac_secret = get_hmac_secret()
    except Exception as exc:
        logger.error("ERR-ARXIV-VERIFY-SECRET: Could not load hmac-secret: %s", exc)
        return _error_page("Server error — please try again later.", 500)

    try:
        payload = verify_token(token_str, PURPOSE_VERIFY, hmac_secret)
    except TokenExpiredError:
        logger.info("Verify: expired token")
        return _error_page(
            "This confirmation link has expired (links are valid for 48 hours).",
            400,
        )
    except TokenInvalidError as exc:
        logger.warning("Verify: invalid token: %s", exc)
        return _error_page("This confirmation link is invalid.", 400)

    email = payload.get("e", "")
    if not email:
        return _error_page("Invalid token payload.", 400)

    # ── Look up subscriber doc ─────────────────────────────────────────────
    email_hash = hashlib.sha256(email.encode()).hexdigest()
    doc_ref = subscribers_col().document(email_hash)

    try:
        doc = doc_ref.get()
    except Exception as exc:
        logger.error("ERR-ARXIV-VERIFY-FIRESTORE: Read failed: %s", exc)
        return _error_page("Server error — please try again later.", 500)

    if not doc.exists:
        # Doc may have been cleaned up or email re-submitted
        logger.info("Verify: doc not found for hash %s", email_hash[:8])
        return _error_page(
            "We couldn't find a pending signup for this link — it may have already been confirmed.",
            404,
        )

    data = doc.to_dict()

    if data.get("verified"):
        # Already verified — idempotent, show success
        logger.info("Verify: already verified, hash=%s", email_hash[:8])
        return _success_page()

    # ── Verify token hash matches stored hash ──────────────────────────────
    token_hash = hashlib.sha256(token_str.encode()).hexdigest()
    stored_hash = data.get("verify_token_hash", "")
    if token_hash != stored_hash:
        logger.warning("Verify: token hash mismatch for hash %s", email_hash[:8])
        return _error_page(
            "This link has been superseded — please use the most recent confirmation email.",
            400,
        )

    # ── Mark verified ──────────────────────────────────────────────────────
    from datetime import datetime, timezone
    try:
        doc_ref.update({
            "verified": True,
            "verified_at": datetime.now(timezone.utc),
        })
        logger.info("Subscriber verified: hash=%s", email_hash[:8])
    except Exception as exc:
        logger.error("ERR-ARXIV-VERIFY-FIRESTORE: Update failed: %s", exc)
        return _error_page("Server error confirming subscription — please try again.", 500)

    return _success_page()


def _success_page() -> tuple:
    body = """<p>Your subscription is confirmed. You'll get your first digest next Monday morning.
Every email includes a one-click unsubscribe link.</p>
<p class="muted">— Silke</p>"""
    return _html_page("You're on the list", "You're on the list", body)
