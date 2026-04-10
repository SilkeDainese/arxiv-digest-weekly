"""Cloud Function: prep_and_preview

Runs Saturday 20:00 CET via Cloud Scheduler.

1. Fetches this week's arXiv papers
2. Scores them globally
3. Stores result in /pending_digest/{week_iso}
4. Builds a preview email (top 10 papers, one example digest, subscriber breakdown)
5. Sends preview to silke.dainese@gmail.com
6. Logs preview_sent_at
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import functions_framework

from shared.ai_scorer import score_papers_with_ai
from shared.arxiv_fetcher import (
    build_personalized_digest,
    fetch_weekly_papers,
    pre_filter_for_ai,
    score_papers_for_all_topics,
)
from shared.email_builder import build_preview_email
from shared.firestore_client import (
    get_all_subscribers,
    mark_preview_sent,
    set_pending_digest,
)
from shared.gmail_client import build_message, send_message
from shared.secrets import get_hmac_secret
from shared.tokens import PURPOSE_CANCEL_SEND, generate_token
from shared.week_utils import build_function_url, build_logs_url, current_week_iso

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "silke-hub")
REGION = os.environ.get("FUNCTION_REGION", "europe-west1")
PREVIEW_RECIPIENT = "silke.dainese@gmail.com"


@functions_framework.http
def prep_and_preview(request):
    """HTTP-triggered Cloud Function (also invokable by Cloud Scheduler via HTTP POST)."""
    logger.info("prep_and_preview triggered")

    week_iso = current_week_iso()

    # ── 1. Fetch papers ────────────────────────────────────────────────────
    logger.info("Fetching arXiv papers for week %s", week_iso)
    papers = fetch_weekly_papers()
    logger.info("Fetched %d papers", len(papers))

    scored_papers = score_papers_for_all_topics(papers)
    logger.info("Scored %d papers globally (keyword)", len(scored_papers))

    # ── 1b. Pre-filter: top 50 by global_score before AI scoring ──────────────
    # Caps token usage and prevents Cloud Function timeout.
    # Papers with global_score == 0 are excluded from AI scoring entirely.
    ai_candidates = pre_filter_for_ai(scored_papers)
    logger.info(
        "Pre-filter: %d papers → %d sent to AI scorer (top 50 by global_score)",
        len(scored_papers), len(ai_candidates),
    )

    # ── 1c. AI scoring cascade (Claude → Vertex Gemini → Gemini API → keyword) ──
    # Enriches each paper with plain_summary, highlight_phrase, score_tier.
    # Falls through gracefully if all API keys are missing or secrets not yet populated.
    ai_scored = score_papers_with_ai(ai_candidates)

    # Merge AI-scored candidates back into the full scored_papers list.
    # Papers that were not sent to AI (below pre-filter threshold) keep their
    # keyword-only global_score and will get keyword fallback scoring at send time.
    ai_scored_by_id = {p["id"]: p for p in ai_scored}
    scored_papers = [
        ai_scored_by_id.get(p["id"], p) for p in scored_papers
    ]

    ai_count = sum(1 for p in scored_papers if p.get("score_tier") in ("ai", "claude", "gemini-vertex", "gemini-api"))
    kw_count = len(scored_papers) - ai_count
    logger.info("AI scoring complete: %d ai-scored, %d keyword-scored", ai_count, kw_count)

    # ── 2. Store in Firestore ──────────────────────────────────────────────
    set_pending_digest(week_iso, {
        "papers": scored_papers,
        "generated_at": datetime.now(timezone.utc),
        "hold_monday_send": False,
    })
    logger.info("Pending digest written to Firestore: week=%s", week_iso)

    # ── 3. Load subscribers for breakdown and example ──────────────────────
    subscribers = get_all_subscribers()
    subscriber_count = len(subscribers)

    # Count subscribers per topic (no PII logged)
    topic_breakdown: dict[str, int] = {}
    example_subscriber = None
    for sub in subscribers:
        for topic in sub.get("topics", []):
            topic_breakdown[topic] = topic_breakdown.get(topic, 0) + 1
        if example_subscriber is None and sub.get("topics"):
            example_subscriber = sub

    # ── 4. Build cancel token ──────────────────────────────────────────────
    hmac_secret = get_hmac_secret()
    cancel_token = generate_token(
        PREVIEW_RECIPIENT,
        PURPOSE_CANCEL_SEND,
        hmac_secret,
        week_iso=week_iso,
    )
    cancel_base_url = build_function_url(PROJECT_ID, REGION, "cancel_send")
    cancel_url = f"{cancel_base_url}?t={cancel_token}&week={week_iso}"
    logs_url = build_logs_url(PROJECT_ID, "prep_and_preview")

    # ── 5. Build example personalized digest (HTML fragment only) ──────────
    example_html = None
    if example_subscriber and scored_papers:
        example_papers = build_personalized_digest(
            scored_papers,
            example_subscriber.get("topics", []),
        )
        if example_papers:
            from shared.email_builder import _paper_html
            example_html = "".join(_paper_html(p) for p in example_papers[:5])

    # ── 6. Build and send preview email ───────────────────────────────────
    subject, html_body, text_body = build_preview_email(
        papers=scored_papers,
        subscriber_count=subscriber_count,
        topic_breakdown=topic_breakdown,
        week_iso=week_iso,
        cancel_url=cancel_url,
        logs_url=logs_url,
        example_digest_html=example_html,
    )

    msg = build_message(
        to_email=PREVIEW_RECIPIENT,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    send_message(msg)
    logger.info("Preview email sent to %s", PREVIEW_RECIPIENT)

    # ── 7. Log preview_sent_at ─────────────────────────────────────────────
    mark_preview_sent(week_iso, datetime.now(timezone.utc))

    return (
        f"prep_and_preview complete: {len(scored_papers)} papers, "
        f"{subscriber_count} subscribers, preview sent.",
        200,
    )
