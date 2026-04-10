"""Cloud Function: send_digest

Runs Monday 07:00 CET via Cloud Scheduler.

1. Reads /pending_digest/{this_week_iso}
2. If hold_monday_send == True: log and exit (no emails sent)
3. If doc missing: log error and exit
4. Otherwise: build and send personalized digest to each subscriber
5. Write /sent_log entries and update subscriber last_sent timestamps
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import functions_framework

from shared.arxiv_fetcher import build_personalized_digest
from shared.email_builder import build_personalized_digest_email
from shared.firestore_client import (
    get_all_subscribers,
    get_pending_digest,
    log_sent,
    update_subscriber_last_sent,
)
from shared.gmail_client import GmailSendError, build_message, send_message
from shared.secrets import get_hmac_secret
from shared.tokens import PURPOSE_MANAGE, PURPOSE_UNSUBSCRIBE, generate_token
from shared.week_utils import build_function_url, current_week_iso

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "silke-hub")
REGION = os.environ.get("FUNCTION_REGION", "europe-west1")


@functions_framework.http
def send_digest(request):
    """HTTP-triggered Cloud Function (also invokable by Cloud Scheduler via HTTP POST)."""
    logger.info("send_digest triggered")

    week_iso = current_week_iso()

    # ── 1. Read pending digest ─────────────────────────────────────────────
    pending = get_pending_digest(week_iso)

    if pending is None:
        msg = f"No pending digest for week {week_iso}. Did prep_and_preview run?"
        logger.error(msg)
        return msg, 500

    # ── 2. Check hold flag ─────────────────────────────────────────────────
    if pending.get("hold_monday_send", False):
        msg = f"HOLD flag set for week {week_iso}. Skipping send. No emails sent."
        logger.info(msg)
        return msg, 200

    papers = pending.get("papers", [])
    logger.info("Loaded %d papers from pending digest for week %s", len(papers), week_iso)

    # ── 3. Load subscribers ────────────────────────────────────────────────
    subscribers = get_all_subscribers()
    logger.info("Loaded %d subscribers", len(subscribers))

    if not subscribers:
        logger.info("No subscribers, nothing to do.")
        return "No subscribers.", 200

    hmac_secret = get_hmac_secret()
    unsub_base = build_function_url(PROJECT_ID, REGION, "unsubscribe")
    manage_base = build_function_url(PROJECT_ID, REGION, "manage")

    sent_count = 0
    failed_count = 0

    # ── 4. Send to each subscriber ────────────────────────────────────────
    for sub in subscribers:
        email = sub.get("email", "")
        doc_id = sub.get("_doc_id", "")
        topics = sub.get("topics", [])

        if not email or not topics:
            domain = email.split("@")[-1] if "@" in email else doc_id
            logger.warning("Skipping subscriber with missing email or topics: doc=%s", doc_id)
            continue

        # Build per-subscriber signed URLs
        unsub_token = generate_token(email, PURPOSE_UNSUBSCRIBE, hmac_secret)
        manage_token = generate_token(email, PURPOSE_MANAGE, hmac_secret)
        unsub_url = f"{unsub_base}?t={unsub_token}"
        manage_url = f"{manage_base}?t={manage_token}"

        # Build personalized paper list
        personalized_papers = build_personalized_digest(papers, topics)

        subject, html_body, text_body = build_personalized_digest_email(
            papers=personalized_papers,
            subscriber_topics=topics,
            week_iso=week_iso,
            unsubscribe_url=unsub_url,
            manage_url=manage_url,
        )

        msg = build_message(
            to_email=email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            unsubscribe_url=unsub_url,
            manage_url=manage_url,
        )

        try:
            send_message(msg)
            update_subscriber_last_sent(doc_id, datetime.now(timezone.utc))
            log_sent(email, week_iso, len(personalized_papers), "sent")
            sent_count += 1
            # Log domain only, not full address
            domain = email.split("@")[-1]
            logger.info("Sent digest to @%s (%d papers)", domain, len(personalized_papers))

        except GmailSendError as exc:
            domain = email.split("@")[-1]
            logger.error("Failed to send to @%s: %s", domain, str(exc))
            log_sent(email, week_iso, len(personalized_papers), "failed", error=str(exc))
            failed_count += 1

    summary = (
        f"send_digest complete for {week_iso}: "
        f"{sent_count} sent, {failed_count} failed."
    )
    logger.info(summary)

    status_code = 200 if failed_count == 0 else 207
    return summary, status_code
