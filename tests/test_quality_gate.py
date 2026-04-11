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


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3: quality gate must also catch low ai_score and zero subscriber_score
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreFloorQualityGate:
    """Fix 3: papers with ai_score < 3.0 (AI-scored) or subscriber_score == 0
    (keyword-only) must be rejected by the quality gate before send."""

    def make_low_ai_score_paper(self, score: float = 2.0) -> dict:
        return {
            "id": "2501.00099",
            "title": "Barely relevant paper",
            "plain_summary": "A marginally relevant method for edge cases in stellar photometry yields inconclusive results.",
            "highlight_phrase": "marginal method tested",
            "ai_score": score,
            "score_tier": "ai",
            "subscriber_score": 0.1,
        }

    def make_zero_subscriber_score_paper(self) -> dict:
        return {
            "id": "2501.00098",
            "title": "Keyword fallback paper",
            "plain_summary": "A paper about nothing in particular related to astronomy at all.",
            "highlight_phrase": "unrelated paper here",
            "ai_score": 0.0,
            "score_tier": "keyword",
            "subscriber_score": 0.0,
        }

    def test_paper_with_low_ai_score_rejected_by_validate_paper_quality(self):
        """validate_paper_quality must fail papers with ai_score < 3.0 (score_tier='ai')."""
        from shared.quality_gate import validate_paper_quality
        paper = self.make_low_ai_score_paper(score=2.0)
        ok, reason = validate_paper_quality(paper)
        assert ok is False, (
            f"Expected paper with ai_score=2.0 to fail quality gate, but it passed"
        )
        assert "ai_score" in reason or "score" in reason.lower(), (
            f"Failure reason should mention score, got: {reason!r}"
        )

    def test_paper_with_ai_score_exactly_3_passes(self):
        """ai_score=3.0 is the floor — paper must pass quality gate."""
        from shared.quality_gate import validate_paper_quality
        paper = self.make_low_ai_score_paper(score=3.0)
        ok, reason = validate_paper_quality(paper)
        assert ok is True, f"Expected ai_score=3.0 to pass, got failure: {reason!r}"

    def test_keyword_paper_with_zero_subscriber_score_rejected(self):
        """Keyword-only paper with subscriber_score=0 must fail the quality gate."""
        from shared.quality_gate import validate_paper_quality
        paper = self.make_zero_subscriber_score_paper()
        ok, reason = validate_paper_quality(paper)
        assert ok is False, (
            "Expected keyword paper with subscriber_score=0 to fail quality gate"
        )

    def test_keyword_paper_with_positive_subscriber_score_passes(self):
        """Keyword paper with subscriber_score > 0 must pass (if summary/phrase are good)."""
        from shared.quality_gate import validate_paper_quality
        paper = {
            "id": "2501.00097",
            "title": "Exoplanet paper",
            "plain_summary": "Radial velocity measurement of exoplanet transit timing variations.",
            "highlight_phrase": "exoplanet timing constraints",
            "ai_score": 15.0,
            "score_tier": "keyword",
            "subscriber_score": 15.0,
        }
        ok, reason = validate_paper_quality(paper)
        assert ok is True, f"Expected keyword paper with score>0 to pass, got: {reason!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 4: Quality-gate ceiling — reject oversize summaries
# ─────────────────────────────────────────────────────────────────────────────

class TestSummaryCeilingQualityGate:
    """Change 3: plain_summary > 320 chars must be rejected by the quality gate."""

    def _make_ai_paper(self, summary: str) -> dict:
        return {
            "id": "2501.99999",
            "title": "Stellar evolution test paper",
            "plain_summary": summary,
            "highlight_phrase": "stellar test result here",
            "ai_score": 7.0,
            "score_tier": "ai",
            "subscriber_score": 50.0,
        }

    def test_summary_exactly_320_chars_passes(self):
        """Boundary: 320 chars is within the ceiling and must pass."""
        summary = "A" * 319 + "."   # 320 chars total, valid sentence
        paper = self._make_ai_paper(summary)
        ok, reason = validate_paper_quality(paper)
        assert ok is True, f"Expected 320-char summary to pass, got: {reason!r}"

    def test_summary_321_chars_fails(self):
        """One char over ceiling must fail."""
        summary = "A" * 320 + "."   # 321 chars total
        paper = self._make_ai_paper(summary)
        ok, reason = validate_paper_quality(paper)
        assert ok is False, "Expected 321-char summary to fail quality gate"
        assert "too long" in reason.lower(), (
            f"Failure reason should mention 'too long', got: {reason!r}"
        )

    def test_summary_321_chars_reason_mentions_char_count(self):
        """Failure reason must include the actual char count and ceiling."""
        summary = "A" * 320 + "."   # 321 chars
        paper = self._make_ai_paper(summary)
        ok, reason = validate_paper_quality(paper)
        assert "321" in reason, f"Reason should mention 321 chars, got: {reason!r}"

    def test_clean_200_char_summary_passes(self):
        """A well-formed 200-char summary must pass ceiling and floor checks."""
        summary = "New ML approach for stellar Teff — synthetic MARCS training, recovers within 50K on APOGEE. Struggles below [Fe/H] = -2 but solid across main sequence targets."
        assert 40 <= len(summary) <= 320
        paper = self._make_ai_paper(summary)
        ok, reason = validate_paper_quality(paper)
        assert ok is True, f"Expected clean summary to pass, got: {reason!r}"

    def test_ceiling_check_does_not_break_existing_floor_check(self):
        """Ceiling check must not suppress the existing floor check (< 40 chars)."""
        short_summary = "Too short."   # 10 chars — below floor
        paper = self._make_ai_paper(short_summary)
        ok, reason = validate_paper_quality(paper)
        assert ok is False
        assert "too short" in reason.lower() or "minimum" in reason.lower(), (
            f"Floor check lost, got: {reason!r}"
        )

    def test_ceiling_check_does_not_break_existing_banned_opener_check(self):
        """Ceiling check must not suppress banned-opener check."""
        banned_summary = "We present a new method for stellar masses using asteroseismology. Works well."
        paper = self._make_ai_paper(banned_summary)
        ok, reason = validate_paper_quality(paper)
        assert ok is False
        assert "banned" in reason.lower() or "opener" in reason.lower(), (
            f"Banned-opener check lost, got: {reason!r}"
        )
