"""Cloud Function: subscribe

POST /subscribe
Body: {"email": "student@example.com", "topics": ["stars", "exoplanets"], "max_papers": 6}

Flow:
  1. Validate email format, topics list, and max_papers
  2. Write Firestore doc at subscribers/{sha256(email)}:
       {email, topics, max_papers, created_at, verified: True, source}
  3. Send welcome email via Gmail API
  4. Return {ok: true}

No double opt-in: students are a known cohort, so we write verified:True directly
and send a plain welcome email.

GDPR:
  - Doc ID is SHA-256(email) — no email in doc ID
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Simple email regex — rejects obviously broken addresses, not a full RFC 5322 parser
_EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,253}\.[^\s@]{2,}$")

# CORS origin
ALLOWED_ORIGIN = "https://silkedainese.github.io"

VALID_TOPICS = frozenset({
    "stars", "exoplanets", "galaxies", "cosmology",
    "high_energy", "instrumentation", "solar_helio", "methods_ml",
})

TOPIC_LABELS = {
    "exoplanets":      "Planets & exoplanets",
    "stars":           "Stars",
    "galaxies":        "Galaxies",
    "cosmology":       "Cosmology",
    "high_energy":     "High-energy astrophysics",
    "instrumentation": "Instrumentation",
    "solar_helio":     "Solar & heliophysics",
    "methods_ml":      "Methods & machine learning",
}

MAX_PAPERS_MIN = 3
MAX_PAPERS_MAX = 15
MAX_PAPERS_DEFAULT = 6


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
    topics_raw = body.get("topics")
    max_papers_raw = body.get("max_papers", MAX_PAPERS_DEFAULT)

    # ── Validate email ─────────────────────────────────────────────────────
    if not email:
        return _error("Email is required", 400, cors)
    if len(email) > 254:
        return _error("Email address is too long", 400, cors)
    if not _EMAIL_RE.match(email):
        return _error("That email looks off — please check and try again.", 400, cors)

    # ── Validate topics ────────────────────────────────────────────────────
    if not topics_raw or not isinstance(topics_raw, list):
        return _error("Please select at least one topic.", 400, cors)
    topics = [t for t in topics_raw if isinstance(t, str)]
    if not topics:
        return _error("Please select at least one topic.", 400, cors)
    invalid = [t for t in topics if t not in VALID_TOPICS]
    if invalid:
        return _error(f"Unknown topic(s): {', '.join(invalid)}", 400, cors)

    # ── Validate max_papers ────────────────────────────────────────────────
    try:
        max_papers = int(max_papers_raw)
    except (ValueError, TypeError):
        max_papers = MAX_PAPERS_DEFAULT
    max_papers = max(MAX_PAPERS_MIN, min(MAX_PAPERS_MAX, max_papers))

    # ── Check for existing doc (idempotent re-signup) ──────────────────────
    email_hash = hashlib.sha256(email.encode()).hexdigest()
    doc_ref = subscribers_col().document(email_hash)
    existing = doc_ref.get()

    if existing.exists:
        # Already subscribed — silently return ok (don't leak whether subscribed)
        logger.info("Re-signup attempt for existing email hash %s", email_hash[:8])
        return _ok(cors)

    # ── Write Firestore doc (verified immediately) ─────────────────────────
    try:
        doc_ref.set({
            "email": email,
            "topics": topics,
            "max_papers": max_papers,
            "created_at": datetime.now(timezone.utc),
            "verified": True,
            "source": "signup_v1",
        })
        logger.info("Subscriber doc created: hash=%s", email_hash[:8])
    except Exception as exc:
        logger.error("ERR-ARXIV-SUBSCRIBE-FIRESTORE: Write failed: %s", exc)
        return _error("Could not save your signup — please try again.", 500, cors)

    # ── Send welcome email ─────────────────────────────────────────────────
    try:
        _send_welcome(email, topics, max_papers)
    except Exception as exc:
        logger.error("ERR-ARXIV-SUBSCRIBE-EMAIL: Gmail send failed: %s", exc)
        # Non-fatal: subscriber is active, show success to user
        return _ok(cors)

    return _ok(cors)


def _send_welcome(email: str, topics: list[str], max_papers: int) -> None:
    """Build and send the welcome email."""
    topic_display = ", ".join(TOPIC_LABELS.get(t, t) for t in topics)
    subject = "You're subscribed to AU student digest"
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
             max-width:520px;margin:40px auto;padding:20px;color:#2B2B2B;line-height:1.6;
             background:#F5F3EE;">
  <div style="font-size:15px;font-weight:600;color:#2C5530;margin-bottom:4px;">AU student digest</div>
  <h2 style="font-size:22px;font-family:Georgia,serif;font-weight:700;margin:16px 0 8px;">
    You're subscribed.
  </h2>
  <p style="color:#555;">Your weekly arXiv digest will arrive every Monday morning.</p>
  <div style="background:#F0EFEB;border-radius:10px;padding:16px 20px;margin:20px 0;">
    <div style="font-size:10px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;
                color:#888;margin-bottom:8px;">YOUR CATEGORIES</div>
    <div style="font-weight:600;font-size:14px;color:#2B2B2B;margin-bottom:4px;">{topic_display}</div>
    <div style="font-size:13px;color:#888;">Max {max_papers} papers per week</div>
  </div>
  <p style="color:#555;font-size:13px;">
    Manage settings or unsubscribe from any digest email.
  </p>
  <hr style="border:none;border-top:1px solid #DDD;margin:28px 0 20px;">
  <p style="font-size:12px;color:#AAA;">
    Made by <a href="mailto:dainese@phys.au.dk" style="color:#AAA;">Silke Dainese</a>
    &middot; dainese@phys.au.dk
  </p>
</body>
</html>"""
    text_body = (
        f"You're subscribed to AU student digest.\n\n"
        f"Your weekly arXiv digest will arrive every Monday morning.\n\n"
        f"Your categories: {topic_display}\n"
        f"Max {max_papers} papers per week\n\n"
        f"Manage settings or unsubscribe from any digest email.\n\n"
        f"— Silke Dainese · dainese@phys.au.dk"
    )
    msg = build_message(
        to_email=email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    send_message(msg)
