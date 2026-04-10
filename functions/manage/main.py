"""Cloud Function: manage

HTTP GET  ?t=<token>         → topic management page with checkboxes
HTTP POST ?t=<token>         → update subscriber topics

Verifies HMAC token. Returns HTML pages for GET and confirmation for POST.
"""
from __future__ import annotations

import logging

import functions_framework

from shared.arxiv_fetcher import TOPIC_KEYWORDS
from shared.email_builder import (
    build_manage_confirmation_page,
    build_manage_page,
)
from shared.firestore_client import get_subscriber_by_email, update_subscriber_topics
from shared.secrets import get_hmac_secret
from shared.tokens import PURPOSE_MANAGE, TokenExpiredError, TokenInvalidError, verify_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Human-readable topic labels
TOPIC_LABELS = {
    "stars": "Stars & Stellar Astrophysics",
    "exoplanets": "Exoplanets & Planetary Systems",
    "galaxies": "Galaxies & Galactic Astrophysics",
    "cosmology": "Cosmology & Large-Scale Structure",
    "high_energy": "High-Energy Astrophysics & Compact Objects",
    "instrumentation": "Instrumentation & Observational Methods",
    "solar_helio": "Solar & Heliospheric Physics",
    "methods_ml": "Statistical Methods & Machine Learning",
}

# Whitelisted topic IDs — only these can be submitted
ALLOWED_TOPICS = set(TOPIC_KEYWORDS.keys())


@functions_framework.http
def manage(request):
    """Manage subscriber topics."""
    token = request.args.get("t", "").strip()

    if not token:
        return _invalid_response("Missing token.")

    try:
        payload = verify_token(token, PURPOSE_MANAGE, get_hmac_secret())
    except TokenExpiredError:
        logger.info("Manage attempted with expired token")
        return _invalid_response("This link has expired. Please use the link from your most recent digest.")
    except TokenInvalidError as exc:
        logger.warning("Manage invalid token: %s", str(exc))
        return _invalid_response("Invalid or expired link.")

    email = payload.get("e", "")
    if not email:
        return _invalid_response("Invalid token payload.")

    sub = get_subscriber_by_email(email)
    if sub is None:
        return _invalid_response("Subscriber not found. You may have already unsubscribed.")

    # ── GET: render the topic management page ─────────────────────────────
    if request.method == "GET":
        current_topics = sub.get("topics", [])
        manage_url = request.url
        html = build_manage_page(
            current_topics=current_topics,
            all_topics=TOPIC_LABELS,
            manage_token=token,
            manage_url=manage_url,
        )
        return (html, 200, {"Content-Type": "text/html; charset=utf-8"})

    # ── POST: update topics ────────────────────────────────────────────────
    if request.method == "POST":
        submitted_topics = request.form.getlist("topics")

        # Validate: only allow whitelisted topic IDs
        clean_topics = [t for t in submitted_topics if t in ALLOWED_TOPICS]

        if not clean_topics:
            return _invalid_response("Please select at least one topic.")

        update_subscriber_topics(sub["_doc_id"], clean_topics)

        domain = email.split("@")[-1]
        logger.info("Topics updated for domain=@%s: %s", domain, clean_topics)

        html = build_manage_confirmation_page()
        return (html, 200, {"Content-Type": "text/html; charset=utf-8"})

    return ("Method not allowed.", 405)


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
