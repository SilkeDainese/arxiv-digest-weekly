"""Fail-closed quality gate for the Monday student send.

Silke's directive: "rather no send than send."

Every paper in the pending digest must have a non-empty plain_summary AND
a non-empty highlight_phrase before any student email goes out. If any paper
fails either check, the entire send is aborted and Silke receives a failure
notification. No partial sends. No fallback to raw abstract.

Additional checks (v2):
  - plain_summary must not start with any banned author-voice opener
  - plain_summary must be at least 40 characters

Additional checks (v3 — Sprint 1 Fix 3):
  - AI-scored papers (score_tier == "ai") must have ai_score >= 3.0
  - Keyword-only papers must have subscriber_score > 0
"""
from __future__ import annotations

from shared.ai_scorer import BANNED_OPENERS

# Minimum character length for a plain_summary to be considered non-stub
_MIN_SUMMARY_LENGTH = 40

# Maximum character length for a plain_summary — enforces the HARD LIMIT from the prompt
_MAX_SUMMARY_LENGTH = 320

# AI relevance floor — papers rated below this are not sent to students
_AI_SCORE_FLOOR = 3.0


def _starts_with_banned_opener(text: str) -> bool:
    """Return True if *text* starts (case-insensitively) with any banned opener."""
    lowered = text.lower().lstrip()
    return any(lowered.startswith(opener) for opener in BANNED_OPENERS)


def validate_paper_quality(paper: dict) -> tuple[bool, str]:
    """Check that a single paper has the required AI output fields.

    Returns:
        (True, "")           — paper passes
        (False, reason_str)  — paper fails, reason_str describes why

    Checks:
      - plain_summary: present, non-empty after stripping whitespace
      - plain_summary: at least 40 characters (not a stub)
      - plain_summary: must not start with a banned author-voice opener
      - highlight_phrase: present, non-empty after stripping whitespace
      - ai_score >= 3.0 for AI-scored papers (score_tier == "ai")
      - subscriber_score > 0 for keyword-only papers (score_tier == "keyword")
    """
    paper_id = paper.get("id", "<unknown>")
    failures = []

    summary = paper.get("plain_summary", None)
    summary_str = str(summary).strip() if summary is not None else ""

    if summary is None or not summary_str:
        failures.append(f"paper {paper_id}: plain_summary is missing or empty")
    else:
        if len(summary_str) < _MIN_SUMMARY_LENGTH:
            failures.append(
                f"paper {paper_id}: plain_summary is too short "
                f"({len(summary_str)} chars, minimum {_MIN_SUMMARY_LENGTH})"
            )
        if len(summary_str) > _MAX_SUMMARY_LENGTH:
            failures.append(
                f"paper {paper_id}: plain_summary too long: "
                f"{len(summary_str)} chars exceeds {_MAX_SUMMARY_LENGTH} ceiling"
            )
        if _starts_with_banned_opener(summary_str):
            failures.append(
                f"paper {paper_id}: plain_summary starts with a banned author-voice opener"
            )

    phrase = paper.get("highlight_phrase", None)
    if phrase is None or not str(phrase).strip():
        failures.append(f"paper {paper_id}: highlight_phrase is missing or empty")

    # Score floor checks (Sprint 1 Fix 3)
    score_tier = paper.get("score_tier")
    if score_tier == "ai":
        ai_score = paper.get("ai_score")
        if ai_score is not None:
            try:
                if float(ai_score) < _AI_SCORE_FLOOR:
                    failures.append(
                        f"paper {paper_id}: ai_score {ai_score} is below floor {_AI_SCORE_FLOOR}"
                    )
            except (TypeError, ValueError):
                failures.append(f"paper {paper_id}: ai_score is not a valid number: {ai_score!r}")
    elif score_tier == "keyword":
        subscriber_score = paper.get("subscriber_score")
        if subscriber_score is not None:
            try:
                if float(subscriber_score) <= 0:
                    failures.append(
                        f"paper {paper_id}: keyword paper has subscriber_score={subscriber_score} (must be > 0)"
                    )
            except (TypeError, ValueError):
                failures.append(
                    f"paper {paper_id}: subscriber_score is not a valid number: {subscriber_score!r}"
                )

    if failures:
        return False, "; ".join(failures)
    return True, ""


def validate_papers_batch(papers: list[dict]) -> list[str]:
    """Validate all papers in a batch.

    Returns:
        List of failure reason strings. Empty list means all papers passed.
    """
    failures: list[str] = []
    for paper in papers:
        ok, reason = validate_paper_quality(paper)
        if not ok:
            failures.append(reason)
    return failures
