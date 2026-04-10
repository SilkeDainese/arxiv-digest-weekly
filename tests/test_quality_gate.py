"""Tests for the fail-closed quality gate in send_digest.

TDD — written before implementation. Tests are expected to fail until
shared/quality_gate.py and the updated functions/mailer/main.py exist.

Silke's directive: "rather no send than send."
If ANY paper in the pending digest is missing plain_summary OR highlight_phrase,
the entire Monday send must abort. No partial sends. No fallback to raw abstract.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from shared.quality_gate import validate_paper_quality, validate_papers_batch


# ─────────────────────────────────────────────────────────────────────────────
# validate_paper_quality — per-paper check
# ─────────────────────────────────────────────────────────────────────────────

def make_good_paper(i: int = 1) -> dict:
    return {
        "id": f"2501.0000{i}",
        "title": f"Paper {i}",
        "plain_summary": "Direct measurement of stellar radii via interferometry.",
        "highlight_phrase": "interferometric radii beat Gaia",
    }


def make_bad_paper_no_summary(i: int = 1) -> dict:
    return {
        "id": f"2501.0000{i}",
        "title": f"Paper {i}",
        "plain_summary": "",  # empty
        "highlight_phrase": "interferometric radii beat Gaia",
    }


def make_bad_paper_no_phrase(i: int = 1) -> dict:
    return {
        "id": f"2501.0000{i}",
        "title": f"Paper {i}",
        "plain_summary": "Good summary text here.",
        "highlight_phrase": "",  # empty
    }


def make_bad_paper_missing_fields(i: int = 1) -> dict:
    return {
        "id": f"2501.0000{i}",
        "title": f"Paper {i}",
        # plain_summary and highlight_phrase not present at all
    }


class TestValidatePaperQuality:
    def test_good_paper_passes(self):
        ok, reason = validate_paper_quality(make_good_paper())
        assert ok is True
        assert reason == ""

    def test_empty_plain_summary_fails(self):
        ok, reason = validate_paper_quality(make_bad_paper_no_summary())
        assert ok is False
        assert "plain_summary" in reason

    def test_empty_highlight_phrase_fails(self):
        ok, reason = validate_paper_quality(make_bad_paper_no_phrase())
        assert ok is False
        assert "highlight_phrase" in reason

    def test_missing_plain_summary_field_fails(self):
        ok, reason = validate_paper_quality(make_bad_paper_missing_fields())
        assert ok is False
        assert "plain_summary" in reason

    def test_whitespace_only_summary_fails(self):
        paper = make_good_paper()
        paper["plain_summary"] = "   "
        ok, reason = validate_paper_quality(paper)
        assert ok is False

    def test_whitespace_only_phrase_fails(self):
        paper = make_good_paper()
        paper["highlight_phrase"] = "\t\n"
        ok, reason = validate_paper_quality(paper)
        assert ok is False

    def test_reason_includes_paper_id(self):
        ok, reason = validate_paper_quality(make_bad_paper_no_summary(i=42))
        # make_bad_paper_no_summary(42) → id = "2501.000042"
        assert "2501.000042" in reason


# ─────────────────────────────────────────────────────────────────────────────
# validate_papers_batch — whole-batch check
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatePapersBatch:
    def test_all_good_returns_empty_failures(self):
        papers = [make_good_paper(i) for i in range(5)]
        failures = validate_papers_batch(papers)
        assert failures == []

    def test_one_bad_returns_one_failure(self):
        papers = [make_good_paper(1), make_bad_paper_no_summary(2), make_good_paper(3)]
        failures = validate_papers_batch(papers)
        assert len(failures) == 1
        assert "2501.00002" in failures[0]

    def test_all_bad_returns_all_failures(self):
        papers = [make_bad_paper_no_summary(i) for i in range(3)]
        failures = validate_papers_batch(papers)
        assert len(failures) == 3

    def test_empty_list_returns_empty_failures(self):
        assert validate_papers_batch([]) == []

    def test_failures_contain_reason_strings(self):
        papers = [make_bad_paper_no_phrase(1)]
        failures = validate_papers_batch(papers)
        assert all(isinstance(f, str) for f in failures)
        assert all(len(f) > 0 for f in failures)


# ─────────────────────────────────────────────────────────────────────────────
# Integration: send_digest aborts when quality gate fails
# ─────────────────────────────────────────────────────────────────────────────

class TestSendDigestQualityGate:
    """send_digest must abort (return 500) and notify Silke if any paper fails the gate."""

    def _run_send_digest(self, papers: list[dict], subscribers: list[dict]):
        """Import and invoke send_digest with mocked dependencies."""
        from functions.mailer.main import send_digest

        pending = {
            "papers": papers,
            "hold_monday_send": False,
        }

        with patch("functions.mailer.main.get_pending_digest", return_value=pending), \
             patch("functions.mailer.main.get_all_subscribers", return_value=subscribers), \
             patch("functions.mailer.main.get_hmac_secret", return_value="fake-secret"), \
             patch("functions.mailer.main.send_message") as mock_send, \
             patch("functions.mailer.main.log_sent"), \
             patch("functions.mailer.main.update_subscriber_last_sent"), \
             patch("functions.mailer.main.build_message", return_value=MagicMock()):
            request = MagicMock()
            response, status = send_digest(request)
            return response, status, mock_send

    def test_abort_when_paper_missing_plain_summary(self):
        bad_paper = make_bad_paper_no_summary(1)
        subscribers = [{"email": "student@au.dk", "topics": ["stars"], "_doc_id": "s1"}]
        response, status, mock_send = self._run_send_digest([bad_paper], subscribers)
        # Must abort — no student emails sent
        assert status in (500, 503)
        # send_message must NOT have been called for student email
        # (the only send allowed is Silke's abort notification)
        student_calls = [
            c for c in mock_send.call_args_list
            if "student@au.dk" in str(c)
        ]
        assert student_calls == [], "Student email was sent despite quality gate failure"

    def test_abort_when_paper_missing_highlight_phrase(self):
        bad_paper = make_bad_paper_no_phrase(1)
        subscribers = [{"email": "student@au.dk", "topics": ["stars"], "_doc_id": "s1"}]
        response, status, _ = self._run_send_digest([bad_paper], subscribers)
        assert status in (500, 503)

    def test_abort_response_mentions_quality_gate(self):
        bad_paper = make_bad_paper_no_summary(1)
        subscribers = [{"email": "student@au.dk", "topics": ["stars"], "_doc_id": "s1"}]
        response, status, _ = self._run_send_digest([bad_paper], subscribers)
        assert "quality" in response.lower() or "plain_summary" in response.lower()

    def test_silke_notified_on_abort(self):
        """On abort, send_digest must send a failure notification to Silke."""
        bad_paper = make_bad_paper_no_summary(1)
        subscribers = [{"email": "student@au.dk", "topics": ["stars"], "_doc_id": "s1"}]

        from functions.mailer.main import send_digest, SILKE_EMAIL

        pending = {"papers": [bad_paper], "hold_monday_send": False}

        notification_recipients = []

        def capture_send(msg):
            # Extract To header to see who it's sent to
            to = msg.get("To", "") if hasattr(msg, "get") else ""
            notification_recipients.append(to)

        with patch("functions.mailer.main.get_pending_digest", return_value=pending), \
             patch("functions.mailer.main.get_all_subscribers", return_value=subscribers), \
             patch("functions.mailer.main.get_hmac_secret", return_value="secret"), \
             patch("functions.mailer.main.log_sent"), \
             patch("functions.mailer.main.update_subscriber_last_sent"), \
             patch("functions.mailer.main.build_message", side_effect=lambda **kw: MagicMock(**{"get.return_value": kw.get("to_email", "")})), \
             patch("functions.mailer.main.send_message", side_effect=capture_send):
            request = MagicMock()
            send_digest(request)

        # At least one notification should go to Silke
        assert any(SILKE_EMAIL in r for r in notification_recipients), \
            f"Silke ({SILKE_EMAIL}) not notified. Recipients seen: {notification_recipients}"

    def test_proceed_when_all_papers_valid(self):
        """All papers valid → send proceeds, subscribers receive emails."""
        good_papers = [make_good_paper(i) for i in range(3)]
        subscribers = [{"email": "student@au.dk", "topics": ["stars"], "_doc_id": "s1"}]
        response, status, mock_send = self._run_send_digest(good_papers, subscribers)
        assert status == 200
        # send_message should have been called (for the student)
        assert mock_send.called
