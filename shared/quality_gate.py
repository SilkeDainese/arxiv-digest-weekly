"""Fail-closed quality gate for the Monday student send.

Silke's directive: "rather no send than send."

Every paper in the pending digest must have a non-empty plain_summary AND
a non-empty highlight_phrase before any student email goes out. If any paper
fails either check, the entire send is aborted and Silke receives a failure
notification. No partial sends. No fallback to raw abstract.
"""
from __future__ import annotations


def validate_paper_quality(paper: dict) -> tuple[bool, str]:
    """Check that a single paper has the required AI output fields.

    Returns:
        (True, "")           — paper passes
        (False, reason_str)  — paper fails, reason_str describes why

    Checks:
      - plain_summary: present, non-empty after stripping whitespace
      - highlight_phrase: present, non-empty after stripping whitespace
    """
    paper_id = paper.get("id", "<unknown>")
    failures = []

    summary = paper.get("plain_summary", None)
    if summary is None or not str(summary).strip():
        failures.append(f"paper {paper_id}: plain_summary is missing or empty")

    phrase = paper.get("highlight_phrase", None)
    if phrase is None or not str(phrase).strip():
        failures.append(f"paper {paper_id}: highlight_phrase is missing or empty")

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
