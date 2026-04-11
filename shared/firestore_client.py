"""Firestore collection accessors.

Collections:
  /subscribers/{auto_id}
  /pending_digest/{week_iso}
  /sent_log/{auto_id}

All PII handling follows the no-PII-in-logs rule:
  - Logs use doc ID or email domain, never full email address.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_db = None


def _get_db():
    global _db
    if _db is None:
        from google.cloud import firestore
        _db = firestore.Client()
    return _db


# ── Collection references ──────────────────────────────────────────────────

def subscribers_col():
    return _get_db().collection("subscribers")


def pending_digest_col():
    return _get_db().collection("pending_digest")


def sent_log_col():
    return _get_db().collection("sent_log")


# ── Subscriber helpers ─────────────────────────────────────────────────────

def get_all_subscribers() -> list[dict[str, Any]]:
    """Return all verified subscriber documents as dicts (includes doc_id).

    Only returns docs where verified=True — unverified (pending confirmation)
    docs are excluded so they don't receive digests.
    """
    docs = subscribers_col().where("verified", "==", True).stream()
    result = []
    for doc in docs:
        data = doc.to_dict()
        data["_doc_id"] = doc.id
        result.append(data)
    return result


def get_subscriber_by_email(email: str) -> Optional[dict[str, Any]]:
    """Find a subscriber doc by email. Returns None if not found."""
    query = subscribers_col().where("email", "==", email).limit(1)
    docs = list(query.stream())
    if not docs:
        return None
    data = docs[0].to_dict()
    data["_doc_id"] = docs[0].id
    return data


def delete_subscriber(doc_id: str) -> None:
    """Delete a subscriber doc by document ID."""
    subscribers_col().document(doc_id).delete()
    # Log domain only, never full email — no PII in logs rule
    logger.info("Subscriber deleted: doc_id=%s", doc_id)


def update_subscriber_topics(doc_id: str, topics: list[str]) -> None:
    """Update a subscriber's topic list."""
    subscribers_col().document(doc_id).update({
        "topics": topics,
    })
    logger.info("Subscriber topics updated: doc_id=%s", doc_id)


def update_subscriber_last_sent(doc_id: str, timestamp: datetime) -> None:
    """Update last_sent timestamp after a digest is delivered."""
    subscribers_col().document(doc_id).update({
        "last_sent": timestamp,
    })


# ── Pending digest helpers ─────────────────────────────────────────────────

def get_pending_digest(week_iso: str) -> Optional[dict[str, Any]]:
    """Read a pending digest document. Returns None if missing."""
    doc = pending_digest_col().document(week_iso).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def set_pending_digest(week_iso: str, data: dict[str, Any]) -> None:
    """Write or overwrite the pending digest for a given week."""
    pending_digest_col().document(week_iso).set(data)
    logger.info("Pending digest written: week=%s, papers=%d", week_iso, len(data.get("papers", [])))


def set_hold_flag(week_iso: str) -> None:
    """Set hold_monday_send = True on the pending digest."""
    pending_digest_col().document(week_iso).update({
        "hold_monday_send": True,
    })
    logger.info("Hold flag set: week=%s", week_iso)


def mark_preview_sent(week_iso: str, timestamp: datetime) -> None:
    """Record when the preview email was sent."""
    pending_digest_col().document(week_iso).update({
        "preview_sent_at": timestamp,
    })


# ── Sent log helpers ───────────────────────────────────────────────────────

def log_sent(
    subscriber_email: str,
    week_iso: str,
    paper_count: int,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Write a sent_log entry.

    Logs email domain only in the application logger (no PII), but stores
    full email in Firestore for operational tracking (access is service-account-only).
    """
    entry: dict[str, Any] = {
        "subscriber_email": subscriber_email,
        "week_iso": week_iso,
        "sent_at": datetime.now(timezone.utc),
        "paper_count": paper_count,
        "status": status,
    }
    if error:
        entry["error"] = error

    sent_log_col().add(entry)

    # Log domain only
    domain = subscriber_email.split("@")[-1] if "@" in subscriber_email else "unknown"
    logger.info(
        "Sent log written: week=%s domain=@%s status=%s papers=%d",
        week_iso, domain, status, paper_count,
    )
