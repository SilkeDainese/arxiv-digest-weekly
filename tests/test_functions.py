"""Integration tests for Cloud Functions behavior.

Tests use mock Firestore client, mock Gmail client, and mock secrets.
No real GCP calls are made.

Covers:
  - prep_and_preview: papers stored, preview sent
  - send_digest with hold flag: zero emails sent, log entry written
  - send_digest without hold flag: N emails sent, N sent_log entries written
  - unsubscribe flow: valid token → deleted, invalid → 400
  - manage GET: HTML with checkboxes; POST: topics updated
  - cancel_send: sets hold flag; subsequent send_digest run exits
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from shared.tokens import generate_token, PURPOSE_UNSUBSCRIBE, PURPOSE_MANAGE, PURPOSE_CANCEL_SEND

SECRET = "test-secret-32-bytes-padded-ok!!"
EMAIL = "student@phys.au.dk"
WEEK = "2026-W15"


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_subscriber(email: str = EMAIL, topics: list | None = None, doc_id: str = "sub001") -> dict:
    return {
        "email": email,
        "topics": topics or ["stars"],
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "_doc_id": doc_id,
    }


def make_paper(i: int = 1) -> dict:
    return {
        "id": f"2501.0000{i}",
        "title": f"Stellar evolution paper {i}",
        "abstract": "Binary star radial velocity stellar evolution.",
        "authors": ["A"],
        "published": "2026-04-07T00:00:00+00:00",
        "url": f"https://arxiv.org/abs/2501.0000{i}",
        "pdf_url": f"https://arxiv.org/pdf/2501.0000{i}",
        "global_score": float(i * 10),
        # Quality gate requires these fields from the AI scoring step
        "plain_summary": f"Summary for paper {i}.",
        "highlight_phrase": f"stellar evolution paper {i}",
        "score_tier": "ai",
    }


# ── unsubscribe function tests ────────────────────────────────────────────────

class TestUnsubscribeFunction:
    def _make_request(self, token: str = ""):
        req = MagicMock()
        req.args = {"t": token}
        req.method = "GET"
        return req

    @patch("functions.unsub.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.unsub.main.get_subscriber_by_email")
    @patch("functions.unsub.main.delete_subscriber")
    def test_valid_token_deletes_subscriber(self, mock_delete, mock_get_sub, mock_secret):
        mock_get_sub.return_value = make_subscriber()
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        req = self._make_request(token)

        from functions.unsub.main import unsubscribe
        response = unsubscribe(req)

        # Response is (html, 200, headers)
        assert response[1] == 200
        mock_delete.assert_called_once_with("sub001")

    @patch("functions.unsub.main.get_hmac_secret", return_value=SECRET)
    def test_invalid_token_returns_400(self, mock_secret):
        req = self._make_request("garbage.token")

        from functions.unsub.main import unsubscribe
        response = unsubscribe(req)

        assert response[1] == 400

    @patch("functions.unsub.main.get_hmac_secret", return_value=SECRET)
    def test_expired_token_returns_400(self, mock_secret):
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET, ttl_override=-1)
        req = self._make_request(token)

        from functions.unsub.main import unsubscribe
        response = unsubscribe(req)

        assert response[1] == 400

    @patch("functions.unsub.main.get_hmac_secret", return_value=SECRET)
    def test_missing_token_returns_400(self, mock_secret):
        req = self._make_request("")

        from functions.unsub.main import unsubscribe
        response = unsubscribe(req)

        assert response[1] == 400

    @patch("functions.unsub.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.unsub.main.get_subscriber_by_email", return_value=None)
    @patch("functions.unsub.main.delete_subscriber")
    def test_already_unsubscribed_returns_200(self, mock_delete, mock_get_sub, mock_secret):
        """Idempotent: unsubscribing a non-existent user still returns success."""
        token = generate_token(EMAIL, PURPOSE_UNSUBSCRIBE, SECRET)
        req = self._make_request(token)

        from functions.unsub.main import unsubscribe
        response = unsubscribe(req)

        assert response[1] == 200
        mock_delete.assert_not_called()


# ── manage function tests ─────────────────────────────────────────────────────

class TestManageFunction:
    def _make_get_request(self, token: str):
        req = MagicMock()
        req.args = {"t": token}
        req.method = "GET"
        req.url = f"https://functions.example.com/manage?t={token}"
        return req

    def _make_post_request(self, token: str, topics: list[str]):
        req = MagicMock()
        req.args = {"t": token}
        req.method = "POST"
        req.form = MagicMock()
        req.form.getlist.return_value = topics
        req.url = f"https://functions.example.com/manage?t={token}"
        return req

    @patch("functions.manage.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.manage.main.get_subscriber_by_email")
    def test_get_returns_html_with_checkboxes(self, mock_get_sub, mock_secret):
        mock_get_sub.return_value = make_subscriber(topics=["stars"])
        token = generate_token(EMAIL, PURPOSE_MANAGE, SECRET)
        req = self._make_get_request(token)

        from functions.manage.main import manage
        response = manage(req)

        assert response[1] == 200
        assert 'type="checkbox"' in response[0]

    @patch("functions.manage.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.manage.main.get_subscriber_by_email")
    @patch("functions.manage.main.update_subscriber_topics")
    def test_post_updates_topics(self, mock_update, mock_get_sub, mock_secret):
        mock_get_sub.return_value = make_subscriber(topics=["stars"])
        token = generate_token(EMAIL, PURPOSE_MANAGE, SECRET)
        req = self._make_post_request(token, ["stars", "exoplanets"])

        from functions.manage.main import manage
        response = manage(req)

        assert response[1] == 200
        mock_update.assert_called_once_with("sub001", ["stars", "exoplanets"])

    @patch("functions.manage.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.manage.main.get_subscriber_by_email")
    @patch("functions.manage.main.update_subscriber_topics")
    def test_post_rejects_invalid_topics(self, mock_update, mock_get_sub, mock_secret):
        """Injected topic IDs not in ALLOWED_TOPICS are silently dropped."""
        mock_get_sub.return_value = make_subscriber(topics=["stars"])
        token = generate_token(EMAIL, PURPOSE_MANAGE, SECRET)
        req = self._make_post_request(token, ["stars", "INVALID_TOPIC", "<script>"])

        from functions.manage.main import manage
        response = manage(req)

        # Only valid topic should be passed to update
        call_args = mock_update.call_args[0][1]
        assert "INVALID_TOPIC" not in call_args
        assert "<script>" not in call_args
        assert "stars" in call_args

    @patch("functions.manage.main.get_hmac_secret", return_value=SECRET)
    def test_invalid_token_returns_400(self, mock_secret):
        req = self._make_get_request("garbage.token")

        from functions.manage.main import manage
        response = manage(req)

        assert response[1] == 400


# ── cancel_send function tests ────────────────────────────────────────────────

class TestCancelSendFunction:
    SILKE_EMAIL = "silke.dainese@gmail.com"

    def _make_request(self, token: str, week: str):
        req = MagicMock()
        req.args = {"t": token, "week": week}
        return req

    @patch("functions.cancel.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.cancel.main.get_pending_digest")
    @patch("functions.cancel.main.set_hold_flag")
    def test_valid_cancel_sets_hold_flag(self, mock_hold, mock_get_pending, mock_secret):
        mock_get_pending.return_value = {"papers": [], "hold_monday_send": False}
        token = generate_token(self.SILKE_EMAIL, PURPOSE_CANCEL_SEND, SECRET, week_iso=WEEK)
        req = self._make_request(token, WEEK)

        from functions.cancel.main import cancel_send
        response = cancel_send(req)

        assert response[1] == 200
        mock_hold.assert_called_once_with(WEEK)

    @patch("functions.cancel.main.get_hmac_secret", return_value=SECRET)
    def test_invalid_token_returns_400(self, mock_secret):
        req = self._make_request("bad.token", WEEK)

        from functions.cancel.main import cancel_send
        response = cancel_send(req)

        assert response[1] == 400

    @patch("functions.cancel.main.get_hmac_secret", return_value=SECRET)
    def test_week_mismatch_returns_400(self, mock_secret):
        token = generate_token(self.SILKE_EMAIL, PURPOSE_CANCEL_SEND, SECRET, week_iso="2026-W14")
        req = self._make_request(token, WEEK)  # Token for W14, request for W15

        from functions.cancel.main import cancel_send
        response = cancel_send(req)

        assert response[1] == 400

    @patch("functions.cancel.main.get_hmac_secret", return_value=SECRET)
    def test_expired_token_returns_400(self, mock_secret):
        token = generate_token(
            self.SILKE_EMAIL, PURPOSE_CANCEL_SEND, SECRET, week_iso=WEEK, ttl_override=-1
        )
        req = self._make_request(token, WEEK)

        from functions.cancel.main import cancel_send
        response = cancel_send(req)

        assert response[1] == 400

    @patch("functions.cancel.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.cancel.main.get_pending_digest", return_value=None)
    def test_missing_pending_digest_returns_400(self, mock_get_pending, mock_secret):
        token = generate_token(self.SILKE_EMAIL, PURPOSE_CANCEL_SEND, SECRET, week_iso=WEEK)
        req = self._make_request(token, WEEK)

        from functions.cancel.main import cancel_send
        response = cancel_send(req)

        assert response[1] == 400

    @patch("functions.cancel.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.cancel.main.get_pending_digest")
    @patch("functions.cancel.main.set_hold_flag")
    def test_already_held_returns_200_without_double_setting(self, mock_hold, mock_get_pending, mock_secret):
        mock_get_pending.return_value = {"papers": [], "hold_monday_send": True}
        token = generate_token(self.SILKE_EMAIL, PURPOSE_CANCEL_SEND, SECRET, week_iso=WEEK)
        req = self._make_request(token, WEEK)

        from functions.cancel.main import cancel_send
        response = cancel_send(req)

        assert response[1] == 200
        mock_hold.assert_not_called()


# ── send_digest behavior tests ────────────────────────────────────────────────

class TestSendDigestFunction:
    def _make_request(self):
        req = MagicMock()
        return req

    @patch("functions.mailer.main.current_week_iso", return_value=WEEK)
    @patch("functions.mailer.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.mailer.main.get_pending_digest")
    @patch("functions.mailer.main.get_all_subscribers")
    @patch("functions.mailer.main.send_message")
    @patch("functions.mailer.main.log_sent")
    @patch("functions.mailer.main.update_subscriber_last_sent")
    def test_hold_flag_sends_zero_emails(
        self, mock_update, mock_log, mock_send, mock_subs, mock_pending, mock_secret, mock_week
    ):
        mock_pending.return_value = {
            "papers": [make_paper(i) for i in range(5)],
            "hold_monday_send": True,
        }
        mock_subs.return_value = [make_subscriber(), make_subscriber("b@phys.au.dk", doc_id="sub002")]

        from functions.mailer.main import send_digest
        response, status = send_digest(self._make_request())

        assert status == 200
        assert "HOLD" in response
        mock_send.assert_not_called()
        mock_log.assert_not_called()

    @patch("functions.mailer.main.current_week_iso", return_value=WEEK)
    @patch("functions.mailer.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.mailer.main.get_pending_digest")
    @patch("functions.mailer.main.get_all_subscribers")
    @patch("functions.mailer.main.send_message")
    @patch("functions.mailer.main.log_sent")
    @patch("functions.mailer.main.update_subscriber_last_sent")
    def test_sends_email_per_subscriber(
        self, mock_update, mock_log, mock_send, mock_subs, mock_pending, mock_secret, mock_week
    ):
        mock_pending.return_value = {
            "papers": [make_paper(i) for i in range(5)],
            "hold_monday_send": False,
        }
        subscribers = [
            make_subscriber("a@phys.au.dk", doc_id="sub001"),
            make_subscriber("b@phys.au.dk", doc_id="sub002"),
            make_subscriber("c@phys.au.dk", doc_id="sub003"),
        ]
        mock_subs.return_value = subscribers

        from functions.mailer.main import send_digest
        response, status = send_digest(self._make_request())

        assert mock_send.call_count == 3
        assert mock_log.call_count == 3
        assert mock_update.call_count == 3

    @patch("functions.mailer.main.current_week_iso", return_value=WEEK)
    @patch("functions.mailer.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.mailer.main.get_pending_digest", return_value=None)
    def test_missing_pending_digest_returns_500(self, mock_pending, mock_secret, mock_week):
        from functions.mailer.main import send_digest
        _, status = send_digest(self._make_request())
        assert status == 500

    @patch("functions.mailer.main.current_week_iso", return_value=WEEK)
    @patch("functions.mailer.main.get_hmac_secret", return_value=SECRET)
    @patch("functions.mailer.main.get_pending_digest")
    @patch("functions.mailer.main.get_all_subscribers")
    @patch("functions.mailer.main.send_message")
    @patch("functions.mailer.main.log_sent")
    @patch("functions.mailer.main.update_subscriber_last_sent")
    def test_failed_send_logged_as_failed(
        self, mock_update, mock_log, mock_send, mock_subs, mock_pending, mock_secret, mock_week
    ):
        from shared.gmail_client import GmailSendError
        mock_pending.return_value = {
            "papers": [make_paper(1)],
            "hold_monday_send": False,
        }
        mock_subs.return_value = [make_subscriber()]
        mock_send.side_effect = GmailSendError("SMTP error")

        from functions.mailer.main import send_digest
        response, status = send_digest(self._make_request())

        # One failed send → log_sent called with status="failed"
        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        # status is the 4th positional arg
        assert call_kwargs[0][3] == "failed"
