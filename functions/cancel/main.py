"""Cloud Function: cancel_send

HTTP GET ?t=<token>&week=<iso_week>

Verifies HMAC token (scoped to Silke's email + week), sets hold flag,
returns confirmation HTML.
"""
from __future__ import annotations

import logging

import functions_framework

from shared.email_builder import build_cancel_confirmation_page
from shared.firestore_client import get_pending_digest, set_hold_flag
from shared.secrets import get_hmac_secret
from shared.tokens import PURPOSE_CANCEL_SEND, TokenExpiredError, TokenInvalidError, verify_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Only Silke can cancel — token is scoped to her email
CANCEL_AUTHORIZED_EMAIL = "silke.dainese@gmail.com"


@functions_framework.http
def cancel_send(request):
    """Set the hold flag to cancel the Monday send."""
    token = request.args.get("t", "").strip()
    week = request.args.get("week", "").strip()

    if not token or not week:
        return _error_response("Missing required parameters.")

    try:
        payload = verify_token(token, PURPOSE_CANCEL_SEND, get_hmac_secret())
    except TokenExpiredError:
        logger.info("cancel_send: expired token for week=%s", week)
        return _error_response(
            "This cancel link has expired (48-hour window). "
            "If you still want to cancel, set hold_monday_send=true manually in Firestore."
        )
    except TokenInvalidError as exc:
        logger.warning("cancel_send: invalid token: %s", str(exc))
        return _error_response("Invalid cancel link.")

    # Verify the token was issued for the correct email and week
    token_email = payload.get("e", "")
    token_week = payload.get("w", "")

    if token_email != CANCEL_AUTHORIZED_EMAIL:
        logger.warning("cancel_send: token issued for wrong email (not Silke)")
        return _error_response("Unauthorized.")

    if token_week != week:
        logger.warning(
            "cancel_send: token week mismatch: token_week=%s, request_week=%s",
            token_week, week,
        )
        return _error_response("Week parameter does not match token.")

    # Check the pending digest exists
    pending = get_pending_digest(week)
    if pending is None:
        logger.warning("cancel_send: no pending digest for week=%s", week)
        return _error_response(
            f"No pending digest found for week {week}. "
            "Either it has not been generated yet, or it was already sent."
        )

    if pending.get("hold_monday_send", False):
        logger.info("cancel_send: hold already set for week=%s", week)
        html = build_cancel_confirmation_page(week)
        return (
            html + "\n<!-- already held -->",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    set_hold_flag(week)
    logger.info("cancel_send: hold flag set for week=%s", week)

    html = build_cancel_confirmation_page(week)
    return (html, 200, {"Content-Type": "text/html; charset=utf-8"})


def _error_response(message: str):
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Error</title></head>
<body style="font-family:Georgia,serif;max-width:480px;margin:80px auto;padding:20px;text-align:center;">
  <h1 style="font-size:20px;">Could not cancel</h1>
  <p>{message}</p>
</body>
</html>"""
    return (html, 400, {"Content-Type": "text/html; charset=utf-8"})
