"""Send Silke a student-identical digest preview email.

Uses the live pending digest in Firestore and the exact same code path
as send_digest — get_pending_digest → build_personalized_digest →
build_personalized_digest_email → build_message → send_message.

The only difference from the Monday mailer: recipient is silke.dainese@gmail.com,
and topics/max_papers are set to match a typical astronomy student subscription.

Usage:
    GOOGLE_CLOUD_PROJECT=silke-hub python scripts/send_preview_to_silke.py

    Optional overrides (env vars):
        PREVIEW_TOPICS=stars,exoplanets,galaxies
        PREVIEW_MAX_PAPERS=6
        PREVIEW_WEEK=2026-W15
"""
from __future__ import annotations

import logging
import os
import sys

# ── Ensure project root is on path ────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

SILKE_EMAIL = "silke.dainese@gmail.com"
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "silke-hub")
REGION = "europe-west1"


def main() -> None:
    # ── Parse optional overrides ──────────────────────────────────────────────
    topics_env = os.environ.get("PREVIEW_TOPICS", "stars,exoplanets")
    topics = [t.strip() for t in topics_env.split(",") if t.strip()]
    max_papers = int(os.environ.get("PREVIEW_MAX_PAPERS", "6"))

    from shared.week_utils import build_function_url, current_week_iso

    week_iso = os.environ.get("PREVIEW_WEEK") or current_week_iso()

    logger.info("Sending preview for week=%s  topics=%s  max_papers=%d  to=%s",
                week_iso, topics, max_papers, SILKE_EMAIL)

    # ── Load pending digest ───────────────────────────────────────────────────
    from shared.firestore_client import get_pending_digest

    pending = get_pending_digest(week_iso)
    if pending is None:
        logger.error("No pending digest for %s. Run prep_and_preview first.", week_iso)
        sys.exit(1)

    hold = pending.get("hold_monday_send", False)
    papers = pending.get("papers", [])
    logger.info("Loaded %d papers (hold=%s)", len(papers), hold)

    if hold:
        logger.warning("hold_monday_send=True on this digest — sending preview anyway (this is a manual preview).")

    # ── Filter to AI-scored papers only ──────────────────────────────────────
    # prep_and_preview may have only partially completed AI scoring.
    # Use only papers that have passed quality gate for this preview.
    from shared.quality_gate import validate_paper_quality

    good_papers = [p for p in papers if validate_paper_quality(p)[0]]
    skipped = len(papers) - len(good_papers)
    if skipped:
        logger.warning(
            "%d/%d papers are missing AI summaries (likely rate-limited during prep).",
            skipped, len(papers),
        )
        logger.warning(
            "MONDAY SEND RISK: send_digest validates the full batch and will abort "
            "if any paper is missing summaries. Re-run prep_and_preview before Monday."
        )
    logger.info("Using %d AI-scored papers for personalization.", len(good_papers))

    # ── Build personalized paper list ─────────────────────────────────────────
    from shared.arxiv_fetcher import build_personalized_digest

    personalized = build_personalized_digest(good_papers, topics, max_papers=max_papers)
    logger.info("Personalized list: %d papers for topics %s", len(personalized), topics)

    # ── Generate HMAC-signed manage/unsubscribe URLs ──────────────────────────
    from shared.secrets import get_hmac_secret
    from shared.tokens import PURPOSE_MANAGE, PURPOSE_UNSUBSCRIBE, generate_token

    hmac_secret = get_hmac_secret()
    unsub_base = build_function_url(PROJECT_ID, REGION, "unsubscribe")
    manage_base = build_function_url(PROJECT_ID, REGION, "manage")
    unsub_token = generate_token(SILKE_EMAIL, PURPOSE_UNSUBSCRIBE, hmac_secret)
    manage_token = generate_token(SILKE_EMAIL, PURPOSE_MANAGE, hmac_secret)
    unsub_url = f"{unsub_base}?t={unsub_token}"
    manage_url = f"{manage_base}?t={manage_token}"

    # ── Build email ───────────────────────────────────────────────────────────
    from shared.email_builder import build_personalized_digest_email

    subject, html_body, text_body = build_personalized_digest_email(
        papers=personalized,
        subscriber_topics=topics,
        week_iso=week_iso,
        unsubscribe_url=unsub_url,
        manage_url=manage_url,
    )

    logger.info("Email subject: %s", subject)

    # ── Build MIME message ─────────────────────────────────────────────────────
    from shared.gmail_client import build_message, send_message

    msg = build_message(
        to_email=SILKE_EMAIL,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        unsubscribe_url=unsub_url,
        manage_url=manage_url,
    )

    # ── Send ──────────────────────────────────────────────────────────────────
    logger.info("Sending to %s ...", SILKE_EMAIL)
    send_message(msg)
    logger.info("Done — preview email sent to %s", SILKE_EMAIL)
    logger.info("Papers included: %d", len(personalized))
    if personalized:
        logger.info("Top paper: %s", personalized[0].get("title", "")[:80])


if __name__ == "__main__":
    main()
