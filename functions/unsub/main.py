"""Cloud Function: unsubscribe

HTTP GET ?t=<token>

Verifies HMAC token, deletes subscriber, returns confirmation HTML.
Invalid or expired tokens return 400 with a neutral message.
"""
from __future__ import annotations

import logging

import functions_framework

from shared.email_builder import build_unsubscribe_page
from shared.firestore_client import get_subscriber_by_email, delete_subscriber
from shared.secrets import get_hmac_secret
from shared.tokens import PURPOSE_UNSUBSCRIBE, TokenExpiredError, TokenInvalidError, verify_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@functions_framework.http
def unsubscribe(request):
    """Process an unsubscribe request."""
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

    # Look up subscriber
    sub = get_subscriber_by_email(email)
    if sub is None:
        # Already unsubscribed or never subscribed — return success anyway
        domain = email.split("@")[-1]
        logger.info("Unsubscribe: subscriber not found (already removed?), domain=@%s", domain)
        return (build_unsubscribe_page(), 200, {"Content-Type": "text/html; charset=utf-8"})

    delete_subscriber(sub["_doc_id"])
    domain = email.split("@")[-1]
    logger.info("Subscriber unsubscribed: domain=@%s", domain)

    return (build_unsubscribe_page(), 200, {"Content-Type": "text/html; charset=utf-8"})


def _invalid_response(message: str):
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Invalid link</title></head>
<body style="font-family:Georgia,serif;max-width:480px;margin:80px auto;padding:20px;text-align:center;">
  <h1 style="font-size:20px;">Invalid or expired link</h1>
  <p>{message}</p>
</body>
</html>"""
    return (html, 400, {"Content-Type": "text/html; charset=utf-8"})
