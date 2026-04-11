"""Cloud Function: manage

HTTP GET  ?t=<token>         → styled settings page (topics + max_papers stepper)
HTTP POST ?t=<token>         → update subscriber settings → styled confirmation page

Verifies HMAC token. Returns branded HTML pages.
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

# Human-readable topic labels (design spec order)
TOPIC_LABELS = {
    "exoplanets":      "Planets &amp; exoplanets",
    "stars":           "Stars",
    "galaxies":        "Galaxies",
    "cosmology":       "Cosmology",
    "high_energy":     "High-energy astrophysics",
    "instrumentation": "Instrumentation",
    "solar_helio":     "Solar &amp; heliophysics",
    "methods_ml":      "Methods &amp; machine learning",
}

# Whitelisted topic IDs — only these can be submitted
ALLOWED_TOPICS = set(TOPIC_KEYWORDS.keys())

MAX_PAPERS_MIN = 3
MAX_PAPERS_MAX = 15
MAX_PAPERS_DEFAULT = 6


@functions_framework.http
def manage(request):
    """Manage subscriber settings (topics + max_papers)."""
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

    # ── GET: render the settings page ─────────────────────────────────────
    if request.method == "GET":
        current_topics = sub.get("topics", [])
        current_max = int(sub.get("max_papers", MAX_PAPERS_DEFAULT))
        manage_url = request.url
        html = build_manage_page(
            current_topics=current_topics,
            all_topics=TOPIC_LABELS,
            manage_token=token,
            manage_url=manage_url,
            email=email,
            max_papers=current_max,
        )
        return (html, 200, {"Content-Type": "text/html; charset=utf-8"})

    # ── POST: update settings ─────────────────────────────────────────────
    if request.method == "POST":
        submitted_topics = request.form.getlist("topics")

        # Validate topics
        clean_topics = [t for t in submitted_topics if t in ALLOWED_TOPICS]
        if not clean_topics:
            return _invalid_response("Please select at least one topic.")

        # Parse max_papers with clamping
        try:
            max_papers = int(request.form.get("max_papers", MAX_PAPERS_DEFAULT))
        except (ValueError, TypeError):
            max_papers = MAX_PAPERS_DEFAULT
        max_papers = max(MAX_PAPERS_MIN, min(MAX_PAPERS_MAX, max_papers))

        update_subscriber_topics(sub["_doc_id"], clean_topics, max_papers=max_papers)

        domain = email.split("@")[-1]
        logger.info(
            "Settings updated for domain=@%s: topics=%s max_papers=%d",
            domain, clean_topics, max_papers,
        )

        # Build manage URL (strip POST body, use GET url base)
        manage_url = request.base_url + f"?t={token}"

        html = build_manage_confirmation_page(
            email=email,
            topics=clean_topics,
            max_papers=max_papers,
            manage_url=manage_url,
        )
        return (html, 200, {"Content-Type": "text/html; charset=utf-8"})

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
