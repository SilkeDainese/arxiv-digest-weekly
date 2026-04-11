"""Cloud Function: unsubscribe

Two-step flow:
  GET  ?t=<token>  → show "Confirm unsubscribe" page
  POST ?t=<token>  → actually delete subscriber → show "You've been unsubscribed" page

Verifies HMAC token on both steps. Invalid or expired tokens return 400.
"""
from __future__ import annotations

import logging

import functions_framework

from shared.email_builder import build_unsubscribe_confirm_page, build_unsubscribe_page
from shared.firestore_client import get_subscriber_by_email, delete_subscriber
from shared.secrets import get_hmac_secret
from shared.tokens import PURPOSE_UNSUBSCRIBE, TokenExpiredError, TokenInvalidError, verify_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SIGNUP_URL = "https://silkedainese.github.io/arxiv-digest/"


@functions_framework.http
def unsubscribe(request):
    """Process an unsubscribe request (two-step: confirm then delete)."""
    token = request.args.get("t", "").strip()

    if not token:
        return _invalid_response("Missing token.")

    try:
        payload = verify_token(token, PURPOSE_UNSUBSCRIBE, get_hmac_secret())
    except TokenExpiredError:
        logger.info("Unsubscribe attempted with expired token")
        return _invalid_response("This link has expired. Please use the link from your most recent digest.")
    except TokenInvalidError as exc:
        logger.warning("Unsubscribe invalid token: %s", str(exc))
        return _invalid_response("Invalid or expired link.")

    email = payload.get("e", "")
    if not email:
        return _invalid_response("Invalid token payload.")

    # ── GET: show confirmation page ────────────────────────────────────────
    if request.method == "GET":
        confirm_url = request.url
        html = build_unsubscribe_confirm_page(email=email, confirm_url=confirm_url)
        return (html, 200, {"Content-Type": "text/html; charset=utf-8"})

    # ── POST: actually delete ──────────────────────────────────────────────
    if request.method == "POST":
        sub = get_subscriber_by_email(email)
        if sub is None:
            # Already unsubscribed — show success anyway
            domain = email.split("@")[-1]
            logger.info("Unsubscribe: subscriber not found (already removed?), domain=@%s", domain)
            return (
                build_unsubscribe_page(signup_url=SIGNUP_URL, email=email),
                200,
                {"Content-Type": "text/html; charset=utf-8"},
            )

        delete_subscriber(sub["_doc_id"])
        domain = email.split("@")[-1]
        logger.info("Subscriber unsubscribed: domain=@%s", domain)

        return (
            build_unsubscribe_page(signup_url=SIGNUP_URL, email=email),
            200,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    return ("Method not allowed.", 405)


def _invalid_response(message: str):
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Invalid link</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:480px;margin:80px auto;
padding:20px;text-align:center;color:#2B2B2B;background:#F5F3EE}}</style>
</head>
<body>
  <h1 style="font-size:22px;font-family:Georgia,serif">Invalid or expired link</h1>
  <p style="color:#555;margin-top:12px">{message}</p>
</body>
</html>"""
    return (html, 400, {"Content-Type": "text/html; charset=utf-8"})
